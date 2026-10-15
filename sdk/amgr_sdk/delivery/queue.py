from __future__ import annotations

from collections import deque
from collections.abc import Sequence
import logging
import threading
from typing import Any

from amgr_sdk.delivery.metrics import DeliveryMetrics
from amgr_sdk.exceptions import CaptureError
from amgr_sdk.models.events import FlowEvent

logger = logging.getLogger("amgr_sdk.delivery.queue")


class EventQueue:
    """Thread-safe bounded in-memory queue with observable overflow handling.

    Decouples event capture from network delivery. Enqueue operations complete
    in memory (O(1)) without initiating network calls or waiting for HTTP responses.
    """

    def __init__(
        self,
        capacity: int = 1000,
        overflow_strategy: str = "drop_oldest",
        metrics: DeliveryMetrics | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"Queue capacity must be at least 1, got {capacity}")

        self.capacity = capacity
        self.overflow_strategy = overflow_strategy.strip().lower()
        valid_strategies = ("drop_oldest", "drop_newest", "block", "raise")
        if self.overflow_strategy not in valid_strategies:
            raise ValueError(
                f"Invalid overflow_strategy {overflow_strategy!r}. Must be one of {valid_strategies!r}"
            )

        self.metrics = metrics
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._deque: deque[FlowEvent] = deque()
        self._closed: bool = False

    @property
    def is_closed(self) -> bool:
        """Return True if the queue has been closed against new enqueue operations."""
        with self._lock:
            return self._closed

    def close(self) -> None:
        """Close the queue, preventing further enqueue operations and waking waiting producers."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def put(self, event: FlowEvent, timeout: float | None = None) -> bool:
        """Enqueue a FlowEvent according to the configured overflow strategy.

        Returns True if the event was accepted into the queue, False if dropped or closed.
        """
        with self._condition:
            if self._closed:
                logger.warning(
                    "Delivery queue is closed: rejected event %s",
                    event.event_id,
                )
                if self.metrics is not None:
                    self.metrics.increment_dropped(1)
                return False

            if len(self._deque) >= self.capacity:
                if self.overflow_strategy == "drop_oldest":
                    dropped = self._deque.popleft()
                    if self.metrics is not None:
                        self.metrics.increment_dropped(1)
                    logger.debug(
                        "Delivery queue overflow: dropped oldest event %s (capacity=%d)",
                        dropped.event_id,
                        self.capacity,
                    )
                    self._deque.append(event)
                    if self.metrics is not None:
                        self.metrics.increment_enqueued(1)
                        self.metrics.set_queue_size(len(self._deque))
                    self._condition.notify()
                    return True

                elif self.overflow_strategy == "drop_newest":
                    if self.metrics is not None:
                        self.metrics.increment_dropped(1)
                    logger.debug(
                        "Delivery queue overflow: dropped newest event %s (capacity=%d)",
                        event.event_id,
                        self.capacity,
                    )
                    return False

                elif self.overflow_strategy == "block":
                    if timeout is not None and timeout <= 0:
                        if self.metrics is not None:
                            self.metrics.increment_dropped(1)
                        return False

                    import time

                    start_time = time.monotonic()
                    while len(self._deque) >= self.capacity:
                        if timeout is not None:
                            elapsed = time.monotonic() - start_time
                            wait_remaining = timeout - elapsed
                            if wait_remaining <= 0:
                                if self.metrics is not None:
                                    self.metrics.increment_dropped(1)
                                logger.debug(
                                    "Delivery queue block timeout: dropped event %s",
                                    event.event_id,
                                )
                                return False
                        else:
                            wait_remaining = None

                        has_space = self._condition.wait(timeout=wait_remaining)
                        if self._closed:
                            if self.metrics is not None:
                                self.metrics.increment_dropped(1)
                            return False
                        if not has_space and len(self._deque) >= self.capacity:
                            if self.metrics is not None:
                                self.metrics.increment_dropped(1)
                            return False

                    self._deque.append(event)
                    if self.metrics is not None:
                        self.metrics.increment_enqueued(1)
                        self.metrics.set_queue_size(len(self._deque))
                    self._condition.notify()
                    return True

                elif self.overflow_strategy == "raise":
                    if self.metrics is not None:
                        self.metrics.increment_dropped(1)
                    raise CaptureError(
                        f"Delivery queue is full (capacity={self.capacity}, overflow_strategy='raise')"
                    )

            # Not full
            self._deque.append(event)
            if self.metrics is not None:
                self.metrics.increment_enqueued(1)
                self.metrics.set_queue_size(len(self._deque))
            self._condition.notify()
            return True

    def put_batch(self, events: Sequence[FlowEvent]) -> int:
        """Enqueue multiple events sequentially, returning the count accepted."""
        accepted = 0
        for event in events:
            if self.put(event):
                accepted += 1
        return accepted

    def get_batch(self, max_items: int = 50, timeout: float | None = None) -> list[FlowEvent]:
        """Drain up to max_items events from the front of the queue.

        If the queue is empty and a timeout is specified, waits for incoming events.
        """
        if max_items < 1:
            return []

        with self._condition:
            if not self._deque and timeout is not None and timeout > 0:
                self._condition.wait(timeout=timeout)

            count = min(len(self._deque), max_items)
            if count == 0:
                return []

            batch = [self._deque.popleft() for _ in range(count)]
            if self.metrics is not None:
                self.metrics.set_queue_size(len(self._deque))
            self._condition.notify_all()
            return batch

    def drain_all(self) -> list[FlowEvent]:
        """Atomically drain all events currently in the queue."""
        with self._condition:
            if not self._deque:
                return []
            events = list(self._deque)
            self._deque.clear()
            if self.metrics is not None:
                self.metrics.set_queue_size(0)
            self._condition.notify_all()
            return events

    def clear(self) -> None:
        """Clear all events in the queue."""
        with self._condition:
            dropped_count = len(self._deque)
            self._deque.clear()
            if self.metrics is not None:
                self.metrics.increment_dropped(dropped_count)
                self.metrics.set_queue_size(0)
            self._condition.notify_all()

    def size(self) -> int:
        """Return the current number of events in the queue."""
        with self._lock:
            return len(self._deque)

    def is_empty(self) -> bool:
        """Check if the queue is empty."""
        with self._lock:
            return len(self._deque) == 0

    def is_full(self) -> bool:
        """Check if the queue has reached capacity."""
        with self._lock:
            return len(self._deque) >= self.capacity

    def __len__(self) -> int:
        return self.size()
