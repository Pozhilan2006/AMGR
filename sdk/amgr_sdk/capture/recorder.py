from __future__ import annotations

from collections.abc import Sequence
import logging
from typing import Any
from uuid import UUID, uuid4

from amgr_sdk.context import current_flow_context
from amgr_sdk.models.events import ComponentRef, FlowEvent, sanitize_metadata
from amgr_sdk.transport.base import Transport

logger = logging.getLogger("amgr_sdk.recorder")


class EventRecorder:
    """Dispatches FlowEvents to an injected transport with lineage and hygiene preservation."""

    def __init__(self, transport: Transport, *, strict: bool = False) -> None:
        self._transport = transport
        self.strict = strict

    @property
    def transport(self) -> Transport:
        return self._transport

    def record(self, event: FlowEvent) -> None:
        """Dispatch a single FlowEvent to the underlying transport."""
        try:
            self._transport.send(event)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning("EventRecorder failed to dispatch event %s: %s", event.event_id, exc)

    def record_batch(self, events: Sequence[FlowEvent]) -> None:
        """Dispatch a sequence of FlowEvents to the underlying transport."""
        try:
            self._transport.send_batch(events)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning("EventRecorder failed to dispatch batch of %d events: %s", len(events), exc)

    def record_operation(
        self,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef | None = None,
        duration_ms: float | None = None,
        status: str = "observed",
        data_labels: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> FlowEvent:
        """Construct and record a FlowEvent representing a completed runtime operation.

        Preserves flow_id, correlation_id, and parent_event_id across operations
        using ambient context when not explicitly supplied.
        """
        ctx = current_flow_context()
        resolved_flow_id = flow_id or (ctx.flow_id if ctx else uuid4())
        resolved_corr_id = correlation_id if correlation_id is not None else (ctx.correlation_id if ctx else None)
        resolved_parent_id = parent_event_id if parent_event_id is not None else (ctx.parent_event_id if ctx else None)
        resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
        if resolved_source is None:
            resolved_source = ComponentRef(
                component_id="unknown-source",
                component_type="service",
            )

        sanitized_meta = sanitize_metadata(metadata or {}, redact=True) if metadata else {}

        event = FlowEvent(
            event_id=event_id or uuid4(),
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            source=resolved_source,
            destination=destination,
            action=action,
            data_labels=list(data_labels) if data_labels else [],
            metadata=sanitized_meta,
            status=status,
            duration_ms=duration_ms,
        )
        self.record(event)
        return event