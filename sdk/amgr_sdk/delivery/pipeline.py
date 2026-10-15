from __future__ import annotations

import atexit
from collections.abc import Sequence
import logging
import threading
import time
from typing import Any

from amgr_sdk.config import AMGRConfig
from amgr_sdk.delivery.metrics import DeliveryMetrics
from amgr_sdk.delivery.queue import EventQueue
from amgr_sdk.models.events import FlowEvent
from amgr_sdk.transport.base import BatchTransportError, Transport

logger = logging.getLogger("amgr_sdk.delivery.pipeline")


class DeliveryPipeline:
    """Orchestrates asynchronous batch delivery of queued events to transport."""

    def __init__(
        self,
        transport: Transport,
        config: AMGRConfig | None = None,
        metrics: DeliveryMetrics | None = None,
    ) -> None:
        self.transport = transport
        self.config = config or AMGRConfig.from_env()
        self.metrics = metrics or DeliveryMetrics()

        self.queue = EventQueue(
            capacity=self.config.queue_capacity,
            overflow_strategy=self.config.queue_overflow_strategy,
            metrics=self.metrics,
        )

        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._flush_trigger = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()
        self._atexit_registered = False

    @property
    def is_running(self) -> bool:
        """Check if the background delivery worker is actively running."""
        with self._lock:
            return self._is_running

    def start(self) -> None:
        """Start the background delivery worker thread."""
        with self._lock:
            if self._is_running:
                return

            self._stop_event.clear()
            self._flush_trigger.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="amgr-delivery-worker",
                daemon=True,
            )
            self._is_running = True
            self._worker_thread.start()

            if not self._atexit_registered:
                atexit.register(self._atexit_shutdown)
                self._atexit_registered = True

            logger.debug(
                "DeliveryPipeline started (queue_capacity=%d, batch_size=%d, flush_interval=%.2fs)",
                self.config.queue_capacity,
                self.config.batch_size,
                self.config.flush_interval_seconds,
            )

    def _worker_loop(self) -> None:
        """Continuous background execution loop draining and transmitting batches."""
        while not self._stop_event.is_set():
            # Wait for either flush interval elapsed, manual flush triggered, or stop requested
            self._flush_trigger.wait(timeout=self.config.flush_interval_seconds)
            self._flush_trigger.clear()

            # Drain and dispatch batches while events exist
            while not self._stop_event.is_set():
                batch = self.queue.get_batch(max_items=self.config.batch_size, timeout=0)
                if not batch:
                    break
                self._dispatch_batch(batch)

    def _dispatch_batch(self, batch: list[FlowEvent]) -> bool:
        """Dispatch a single batch to the underlying transport with error & metric tracking."""
        if not batch:
            return True

        try:
            self.transport.send_batch(batch)
            self.metrics.increment_delivered(len(batch))
            return True
        except BatchTransportError as err:
            delivered_count = len(err.successful_events)
            failed_count = 1 + len(err.remaining_events)
            self.metrics.increment_delivered(delivered_count)
            self.metrics.increment_failed(failed_count)
            logger.warning(
                "DeliveryPipeline partial batch failure: %d/%d delivered. Error: %s",
                delivered_count,
                len(batch),
                err.message,
            )
            return False
        except Exception as exc:
            self.metrics.increment_failed(len(batch))
            logger.warning(
                "DeliveryPipeline failed to dispatch batch of %d events: %s",
                len(batch),
                exc.__class__.__name__,
            )
            return False

    def flush(self, timeout: float | None = None) -> bool:
        """Flush all buffered events through the transport within timeout seconds.

        Returns True if the queue was emptied, False if timeout expired with remaining events.
        """
        deadline = time.monotonic() + timeout if timeout is not None else None

        while not self.queue.is_empty():
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "DeliveryPipeline flush timed out with %d events remaining in queue",
                    len(self.queue),
                )
                return False

            batch = self.queue.get_batch(max_items=self.config.batch_size, timeout=0)
            if not batch:
                break
            self._dispatch_batch(batch)

        return self.queue.is_empty()

    def stop(self, timeout: float | None = None) -> int:
        """Gracefully stop the background worker, close queue, and execute a final bounded flush.

        Returns the number of events remaining in the queue (0 on complete drain).
        """
        with self._lock:
            if not self._is_running:
                self.queue.close()
                flush_timeout = timeout if timeout is not None else self.config.shutdown_timeout_seconds
                self.flush(timeout=flush_timeout)
                return len(self.queue)
            self._is_running = False

        self._stop_event.set()
        self._flush_trigger.set()

        # Prevent further enqueue operations during shutdown
        self.queue.close()

        # Join worker thread so it stops executing before final drain
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

        # Bounded final flush for any items remaining in the queue
        flush_timeout = timeout if timeout is not None else self.config.shutdown_timeout_seconds
        self.flush(timeout=flush_timeout)

        remaining = len(self.queue)
        logger.debug("DeliveryPipeline stopped successfully (%d remaining)", remaining)
        return remaining

    def _atexit_shutdown(self) -> None:
        """Graceful shutdown hook invoked upon Python interpreter exit."""
        self.stop(timeout=self.config.shutdown_timeout_seconds)


class QueuedTransport(Transport):
    """Transport implementation that routes events into an asynchronous DeliveryPipeline."""

    def __init__(self, pipeline: DeliveryPipeline) -> None:
        self.pipeline = pipeline

    def send(self, event: FlowEvent) -> None:
        """Enqueue an event for asynchronous batch delivery."""
        self.pipeline.metrics.increment_captured(1)
        accepted = self.pipeline.queue.put(event)
        if accepted and len(self.pipeline.queue) >= self.pipeline.config.batch_size:
            self.pipeline._flush_trigger.set()

    def send_batch(self, events: Sequence[FlowEvent]) -> None:
        """Enqueue a sequence of events for asynchronous batch delivery."""
        for event in events:
            self.send(event)

    def flush(self, timeout: float | None = None) -> bool:
        """Trigger an immediate flush of the delivery pipeline."""
        return self.pipeline.flush(timeout=timeout)

    def close(self, timeout: float | None = None) -> int:
        """Gracefully shut down the underlying delivery pipeline."""
        return self.pipeline.stop(timeout=timeout)
