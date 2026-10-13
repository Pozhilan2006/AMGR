from collections.abc import Sequence
from typing import Any
from uuid import UUID

from amgr_sdk.capture.recorder import EventRecorder
from amgr_sdk.config import AMGRConfig
from amgr_sdk.context import current_flow_context
from amgr_sdk.models.events import ComponentRef, FlowEvent


_default_client: "AMGRClient | None" = None


def get_default_client() -> "AMGRClient | None":
    return _default_client


def set_default_client(client: "AMGRClient | None") -> None:
    global _default_client
    _default_client = client


class AMGRClient:
    """Thin facade over an application-provided event recorder or default HTTP transport."""

    def __init__(
        self,
        recorder: EventRecorder | None = None,
        config: AMGRConfig | None = None,
    ) -> None:
        self.config = config or AMGRConfig.from_env()
        if recorder is None:
            from amgr_sdk.transport.http import HttpTransport

            self._recorder = EventRecorder(
                HttpTransport(self.config),
                sensitive_field_keys=self.config.sensitive_field_keys,
                redact_sensitive_fields=self.config.redact_sensitive_fields,
            )
        else:
            self._recorder = recorder

    @property
    def recorder(self) -> EventRecorder:
        return self._recorder

    def record(self, event: FlowEvent) -> None:
        if not self.config.capture_enabled:
            return
        self._recorder.record(event)

    def record_batch(self, events: Sequence[FlowEvent]) -> None:
        if not self.config.capture_enabled:
            return
        self._recorder.record_batch(events)

    def build_event(
        self,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef | None = None,
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        data_labels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "observed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Construct a validated FlowEvent, inheriting ambient context when fields are omitted."""
        ctx = current_flow_context()
        resolved_flow_id = flow_id or (ctx.flow_id if ctx else None)
        if resolved_flow_id is None:
            from uuid import uuid4

            resolved_flow_id = uuid4()

        resolved_corr_id = correlation_id if correlation_id is not None else (ctx.correlation_id if ctx else None)
        resolved_parent_id = parent_event_id if parent_event_id is not None else (ctx.parent_event_id if ctx else None)
        resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
        if resolved_source is None:
            raise ValueError("Event source must be specified or set in the active FlowContext.")

        from amgr_sdk.models.events import sanitize_metadata

        sanitized_meta = (
            sanitize_metadata(
                metadata,
                additional_sensitive_keys=self.config.sensitive_field_keys,
                redact=self.config.redact_sensitive_fields,
            )
            if metadata
            else {}
        )

        return FlowEvent(
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            source=resolved_source,
            destination=destination,
            action=action,
            data_labels=data_labels or [],
            metadata=sanitized_meta,
            status=status,
            duration_ms=duration_ms,
        )

    def record_event(
        self,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef | None = None,
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        data_labels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "observed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Build and record a FlowEvent in a single step."""
        event = self.build_event(
            action=action,
            destination=destination,
            source=source,
            flow_id=flow_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            data_labels=data_labels,
            metadata=metadata,
            status=status,
            duration_ms=duration_ms,
        )
        self.record(event)
        return event