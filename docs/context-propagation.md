# AMGR Context Propagation Across Boundaries

This guide documents the design and usage patterns for propagating AMGR runtime workflow contexts across execution, process, queue, and service boundaries.

---

## 1. Context Model & Carrier Contracts

AMGR establishes execution correlation through lightweight, immutable context identifiers:

- **`flow_id`**: Identifies the overall logical workflow or distributed transaction.
- **`correlation_id`**: Identifies the external request, execution batch, or session.
- **`parent_event_id`**: Identifies the immediate parent operation in a causal chain. **Never fabricated** when the parent is unknown or across asynchronous message boundaries unless explicitly supplied.
- **`current_component`**: Optional identifier of the active initiating component.
- **`metadata`**: Sanitized operational metadata (e.g. `tenant_id`, `environment`). Sensitive fields (`api_key`, `secret`, `password`, `authorization`) are centrally masked with `[REDACTED]`.

### Supported Carrier Formats

The SDK supports two carrier formats via [`export_context`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py) and [`import_context`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py):

1. **Structured Dictionary (`as_headers=False`, default)**:
   Suitable for JSON payloads, task arguments, message bodies, and structured logs:
   ```json
   {
     "flow_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
     "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
     "parent_event_id": "1c7b8d4e-1234-5678-abcd-ef0123456789",
     "metadata": {
       "tenant_id": "tenant-42"
     }
   }
   ```
2. **Header Carrier (`as_headers=True`)**:
   Suitable for HTTP request headers, AMQP message headers, and Kafka record headers:
   ```http
   X-AMGR-Flow-ID: 9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d
   X-AMGR-Correlation-ID: 9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d
   X-AMGR-Parent-Event-ID: 1c7b8d4e-1234-5678-abcd-ef0123456789
   ```

---

## 2. Public API Summary

- **[`amgr.export_context(context=None, *, as_headers=False)`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py)**:
  Exports the ambient (or supplied) context into a safe serializable dictionary. Returns `{}` if no context is active.
- **[`amgr.import_context(carrier, *, source=None, metadata=None)`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py)** (alias **`restore_context`**):
  Restores an execution context from a carrier dictionary, header mapping, or `FlowContext` object. Operates as a synchronous context manager, asynchronous context manager, or decorator.
- **[`amgr.inject_context(carrier, context=None)`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py)**:
  Injects AMGR correlation headers into a mutable dictionary (e.g. HTTP headers dict).
- **[`amgr.extract_context(carrier)`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py)**:
  Extracts and returns a [`FlowContext`](file:///c:/AMGR-main/AMGR-main/sdk/amgr_sdk/context.py) object without activating it.

---

## 3. Deliberate Propagation Patterns

### Pattern A: Background Tasks (Thread Boundaries)

When dispatching work to background thread pools or worker threads, capture the carrier on the originating thread and restore it inside the worker function:

```python
from concurrent.futures import ThreadPoolExecutor
from amgr_sdk import export_context, restore_context, capture_tool

executor = ThreadPoolExecutor(max_workers=4)

@capture_tool("async_data_processor")
def process_data(data: dict):
    # Instrumented tool running with propagated context
    ...

def worker(carrier: dict, data: dict):
    # Establish context in the worker thread
    with restore_context(carrier):
        return process_data(data)

def handle_request(payload: dict):
    # Originating thread exports safe carrier
    carrier = export_context()
    future = executor.submit(worker, carrier, payload)
    return future
```

### Pattern B: Async Tasks (`asyncio.create_task`)

When scheduling independent asynchronous background tasks that may outlive the parent request scope:

```python
import asyncio
from amgr_sdk import export_context, restore_context, capture_agent

@capture_agent("background_indexer")
async def index_document(doc_id: str):
    ...

async def background_worker(carrier: dict, doc_id: str):
    # Re-establish ambient context in background coroutine
    async with restore_context(carrier):
        await index_document(doc_id)

async def on_upload(doc_id: str):
    # Export current context
    carrier = export_context()
    # Spawn background task
    asyncio.create_task(background_worker(carrier, doc_id))
```

### Pattern C: Message Queues (Celery)

Pass context via Celery task headers or payload arguments:

```python
from celery import shared_task
from amgr_sdk import export_context, restore_context, capture_agent

@shared_task(bind=True)
def async_agent_task(self, item_id: str, amgr_carrier: dict | None = None):
    # Restore context inside worker process
    with restore_context(amgr_carrier, source="celery_worker"):
        run_worker_logic(item_id)

def enqueue_work(item_id: str):
    # Producer captures context
    carrier = export_context()
    async_agent_task.delay(item_id, amgr_carrier=carrier)
```

### Pattern D: Message Brokers (RabbitMQ / AMQP / Kafka)

Pass context via message headers or envelope metadata:

```python
from amgr_sdk import export_context, restore_context

# --- Producer ---
def publish_message(channel, queue_name: str, payload: dict):
    # Export as header dictionary
    headers = export_context(as_headers=True)
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(payload),
        properties=pika.BasicProperties(headers=headers),
    )

# --- Consumer ---
def on_message(channel, method, properties, body):
    # Extract headers and restore context
    headers = properties.headers or {}
    with restore_context(headers, source="amqp_consumer"):
        process_incoming_payload(json.loads(body))
```

---

## 4. Context Isolation and Safety Rules

1. **No Parent Fabrication**: If a background task or message consumer receives a carrier without a `parent_event_id`, AMGR never invents a parent ID. The incoming operation is recorded as a root or unparented event within the valid `flow_id`.
2. **Context Leak Prevention**: `import_context` and `restore_context` utilize token-based context resets (`_current_context.reset(token)`). When worker threads or event loop tasks complete (whether by return or unhandled exception), the ambient context is completely cleared or reverted to its prior state.
3. **Immutability of Caller Inputs**: Caller-supplied carriers and metadata dictionaries are copied and never mutated in place.
4. **Data Redaction Guarantee**: Sensitive credentials (`api_key`, `secret`, `password`, `auth_token`, etc.) are stripped or masked prior to carrier export, preventing leakage into message logs or queue backplanes.
