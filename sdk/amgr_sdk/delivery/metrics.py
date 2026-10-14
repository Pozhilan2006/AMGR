from __future__ import annotations

import threading
from typing import Any


class DeliveryMetrics:
    """Thread-safe telemetry counters for tracking AMGR event delivery pipeline state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._captured: int = 0
        self._enqueued: int = 0
        self._delivered: int = 0
        self._failed: int = 0
        self._dropped: int = 0
        self._queue_size: int = 0

    @property
    def captured(self) -> int:
        """Total number of events captured by recorder and client."""
        with self._lock:
            return self._captured

    @property
    def enqueued(self) -> int:
        """Total number of events accepted into the in-memory delivery queue."""
        with self._lock:
            return self._enqueued

    @property
    def delivered(self) -> int:
        """Total number of events successfully transmitted to transport."""
        with self._lock:
            return self._delivered

    @property
    def failed(self) -> int:
        """Total number of events that failed transmission after all retries."""
        with self._lock:
            return self._failed

    @property
    def dropped(self) -> int:
        """Total number of events dropped due to queue overflow or rejection policy."""
        with self._lock:
            return self._dropped

    @property
    def queue_size(self) -> int:
        """Current count of events waiting in the delivery queue."""
        with self._lock:
            return self._queue_size

    def increment_captured(self, count: int = 1) -> None:
        """Increment captured events counter."""
        if count <= 0:
            return
        with self._lock:
            self._captured += count

    def increment_enqueued(self, count: int = 1) -> None:
        """Increment enqueued events counter."""
        if count <= 0:
            return
        with self._lock:
            self._enqueued += count

    def increment_delivered(self, count: int = 1) -> None:
        """Increment delivered events counter."""
        if count <= 0:
            return
        with self._lock:
            self._delivered += count

    def increment_failed(self, count: int = 1) -> None:
        """Increment failed events counter."""
        if count <= 0:
            return
        with self._lock:
            self._failed += count

    def increment_dropped(self, count: int = 1) -> None:
        """Increment dropped events counter."""
        if count <= 0:
            return
        with self._lock:
            self._dropped += count

    def set_queue_size(self, size: int) -> None:
        """Update current queue size counter."""
        with self._lock:
            self._queue_size = max(0, size)

    def snapshot(self) -> dict[str, int]:
        """Return an atomic snapshot of current delivery metrics counters."""
        with self._lock:
            return {
                "captured": self._captured,
                "enqueued": self._enqueued,
                "delivered": self._delivered,
                "failed": self._failed,
                "dropped": self._dropped,
                "queue_size": self._queue_size,
            }

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._lock:
            self._captured = 0
            self._enqueued = 0
            self._delivered = 0
            self._failed = 0
            self._dropped = 0
            self._queue_size = 0

    def __repr__(self) -> str:
        s = self.snapshot()
        return (
            f"DeliveryMetrics(captured={s['captured']}, enqueued={s['enqueued']}, "
            f"delivered={s['delivered']}, failed={s['failed']}, "
            f"dropped={s['dropped']}, queue_size={s['queue_size']})"
        )
