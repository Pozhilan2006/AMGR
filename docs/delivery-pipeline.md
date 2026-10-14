# AMGR Event Delivery Pipeline & Transport Integration

This document describes the event delivery pipeline architecture, operational modes, configuration parameters, lifecycle controls, and in-memory delivery trade-offs in the AMGR Python SDK.

---

## 1. Delivery Modes

The AMGR SDK provides two delivery modes:

### Synchronous Direct Delivery (Default)
- **Default behavior**: `AMGRClient()` and `AMGRConfig()` operate in direct synchronous mode by default (`buffered = False`).
- **Zero background overhead**: No worker threads or queues are initialized.
- **Immediate delivery**: Events recorded via `record()` or `record_event()` are serialized and transmitted directly to the HTTP backend on the calling thread.
- **Fail-safe capture**: If a network failure occurs, the error is logged and suppressed by default (`strict=False`), ensuring application execution is never blocked or interrupted.

### Buffered Asynchronous Delivery (Opt-In)
- **Decoupled execution**: Events captured by the application are placed into a bounded in-memory queue in $O(1)$ time without making network requests on the critical execution path.
- **Batched transport**: A background worker thread drains queued events into batches up to `batch_size` or every `flush_interval_seconds`.
- **Observable metrics**: Tracks counts for captured, enqueued, delivered, dropped, and failed events, along with current queue size.
- **Explicit activation**: Enabled via `AMGRClient.buffered()`, passing `buffered=True` to `AMGRClient`, or configuring `AMGR_BUFFERED_DELIVERY=true`.

---

## 2. Configuration Parameters

The delivery pipeline is configured via `AMGRConfig` or environment variables:

| Setting | Environment Variable | Default | Description |
|---|---|---|---|
| `buffered` | `AMGR_BUFFERED_DELIVERY` | `false` | Enables asynchronous queue-backed delivery. |
| `queue_capacity` | `AMGR_QUEUE_CAPACITY` | `1000` | Maximum number of events held in the in-memory queue. |
| `batch_size` | `AMGR_BATCH_SIZE` | `50` | Maximum number of events per HTTP delivery batch. |
| `flush_interval_seconds` | `AMGR_FLUSH_INTERVAL_SECONDS` | `1.0` | Maximum interval between background worker flush cycles. |
| `queue_overflow_strategy`| `AMGR_QUEUE_OVERFLOW_STRATEGY`| `"drop_oldest"` | Action taken when queue reaches capacity (`drop_oldest`, `drop_newest`, `block`, `raise`). |
| `shutdown_timeout_seconds`| `AMGR_SHUTDOWN_TIMEOUT_SECONDS`| `5.0` | Maximum seconds allowed for bounded final flush during client shutdown. |
| `backoff_jitter` | `AMGR_BACKOFF_JITTER` | `true` | Adds randomized jitter to exponential retry backoffs. |

---

## 3. Client Usage Patterns

### Pattern A: Standard Synchronous Client (Default)

```python
from amgr_sdk import AMGRClient, ComponentRef

# Instantiates client in synchronous direct delivery mode (zero background threads)
client = AMGRClient()

client.record_event(
    action="user.login",
    destination=ComponentRef("auth-service", "service"),
)
```

### Pattern B: Buffered Client via Factory

```python
from amgr_sdk import AMGRClient, ComponentRef

# Explicitly opt into buffered asynchronous delivery
client = AMGRClient.buffered()

client.record_event(
    action="checkout.complete",
    destination=ComponentRef("order-service", "service"),
)

# Explicitly flush buffered events
client.flush(timeout=2.0)

# Clean shutdown
client.close()
```

### Pattern C: Context Manager Lifecycle

```python
from amgr_sdk import AMGRClient

# Bounded lifecycle ensures automatic final flush and worker cleanup on exit
with AMGRClient.buffered() as client:
    client.record_event(
        action="data.process",
        destination=ComponentRef("worker", "service"),
    )
# client.close() is automatically called upon context exit
```

---

## 4. Lifecycle Operations

| Operation | Signature | Behavior |
|---|---|---|
| `start()` | `client.start() -> None` | Starts the background worker thread. Idempotent and thread-safe. |
| `flush()` | `client.flush(timeout: float \| None = None) -> bool` | Drains all currently queued events through the transport within `timeout` seconds. Returns `True` if fully drained, `False` on timeout. |
| `close()` | `client.close(timeout: float \| None = None) -> int` | Closes the queue against incoming events, joins the worker thread, and executes a bounded final flush. Returns the number of undelivered events remaining (0 on clean exit). |

---

## 5. Queue Overflow Strategies

When the bounded in-memory queue reaches `queue_capacity`:

1. **`drop_oldest` (Default)**: Discards the oldest event in the queue to make room for the incoming event. Preferred for real-time observability where newest traces have higher diagnostic value.
2. **`drop_newest`**: Drops the newly captured event and keeps older buffered events.
3. **`block`**: Halts the producing thread until space becomes available or until timeout expires. Automatically unblocks if the queue is closed during shutdown.
4. **`raise`**: Raises `CaptureError`, notifying the application immediately of queue saturation.

---

## 6. Observability and Delivery Metrics

Clients operating in buffered mode expose `client.metrics` (`DeliveryMetrics`):

```python
metrics = client.metrics
if metrics is not None:
    snapshot = metrics.snapshot()
    print(f"Captured: {snapshot['captured']}")
    print(f"Enqueued: {snapshot['enqueued']}")
    print(f"Delivered: {snapshot['delivered']}")
    print(f"Dropped: {snapshot['dropped']}")
    print(f"Failed: {snapshot['failed']}")
    print(f"Queue Size: {snapshot['queue_size']}")
```

---

## 7. In-Memory Delivery Trade-Offs

- **Memory Boundary**: Bounded queue prevents unconstrained memory consumption regardless of backend latency or ingestion spikes.
- **Process Termination**: In-memory events may be lost if a process terminates abruptly (e.g. `SIGKILL` or power loss). `atexit` registration and `client.close()` provide bounded final-flush protection for normal application termination.
- **Idempotency**: Downstream ingestion backends should treat events idempotently using `event_id` and causal lineage IDs.
