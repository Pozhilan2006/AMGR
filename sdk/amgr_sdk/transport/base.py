from collections.abc import Sequence
from typing import Protocol

from amgr_sdk.exceptions import AMGRError
from amgr_sdk.models.events import FlowEvent


class TransportError(AMGRError):
    """Base exception for transport and dispatch failures."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def __str__(self) -> str:
        if self.status_code is not None:
            return f"[{self.status_code}] {self.message}"
        return self.message


class TransportConnectionError(TransportError):
    """Network connection failure, timeout, or DNS resolution failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=None, retryable=True)


class BatchTransportError(TransportError):
    """Partial failure during batch event dispatch."""

    def __init__(
        self,
        message: str,
        successful_events: list[FlowEvent],
        failed_event: FlowEvent,
        remaining_events: list[FlowEvent],
        cause: TransportError,
    ) -> None:
        super().__init__(
            message=message,
            status_code=cause.status_code,
            retryable=False,
        )
        self.successful_events = successful_events
        self.failed_event = failed_event
        self.remaining_events = remaining_events
        self.cause = cause


class Transport(Protocol):
    def send(self, event: FlowEvent) -> None:
        """Submit one event using an implementation supplied by the application."""

    def send_batch(self, events: Sequence[FlowEvent]) -> None:
        """Submit a sequence of events using an implementation supplied by the application."""