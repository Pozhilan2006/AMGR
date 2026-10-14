from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from amgr_sdk.context import _FlowContextScope, current_flow_context, flow_context
from amgr_sdk.models.events import ComponentRef, FlowEvent, sanitize_metadata
from amgr_sdk.transport.base import Transport

logger = logging.getLogger("amgr_sdk.recorder")


def _resolve_data_labels(labels: Sequence[str] | str | None) -> list[str]:
    """Normalize data classification labels.

    Preserves explicitly supplied non-empty labels.
    If no classification is available, returns ['unknown'].
    """
    if labels is None:
        return ["unknown"]
    if isinstance(labels, str):
        cleaned = labels.strip()
        return [cleaned] if cleaned else ["unknown"]
    cleaned_list: list[str] = []
    for item in labels:
        if isinstance(item, str) and item.strip():
            stripped = item.strip()
            if stripped not in cleaned_list:
                cleaned_list.append(stripped)
    return cleaned_list if cleaned_list else ["unknown"]


class ActiveOperation(AbstractContextManager["ActiveOperation"]):
    """Represents an ongoing in-flight runtime operation.

    Tracks timing, causal lineage, and metadata. Can be completed via
    succeed() or fail(), or managed automatically as a context manager.
    Exceptions within the context manager are recorded and never swallowed.
    """

    def __init__(
        self,
        recorder: EventRecorder,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef,
        data_labels: list[str],
        metadata: dict[str, Any] | None = None,
        flow_id: UUID,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> None:
        self._recorder = recorder
        self.action = action
        self.destination = destination
        self.source = source
        self.data_labels = data_labels
        self.metadata = dict(metadata or {})
        self.flow_id = flow_id
        self.correlation_id = correlation_id
        self.parent_event_id = parent_event_id
        self.event_id = event_id or uuid4()
        self.start_time = time.perf_counter()

        self._scope: _FlowContextScope | None = None
        self._is_completed: bool = False
        self._completed_event: FlowEvent | None = None

    @property
    def is_completed(self) -> bool:
        return self._is_completed

    def add_metadata(self, key: str, value: Any) -> None:
        """Add or update an operational metadata field."""
        self.metadata[key] = value

    def add_data_label(self, label: str) -> None:
        """Add a classification label, removing the fallback 'unknown' label if present."""
        stripped = label.strip()
        if not stripped:
            return
        if self.data_labels == ["unknown"]:
            self.data_labels = [stripped]
        elif stripped not in self.data_labels:
            self.data_labels.append(stripped)

    def enter_context(self) -> None:
        """Activate child flow context associating nested operations with this event."""
        if self._scope is None:
            self._scope = flow_context(
                flow_id=self.flow_id,
                correlation_id=self.correlation_id,
                parent_event_id=self.event_id,
                component=self.destination,
            )
            self._scope.__enter__()

    def exit_context(self) -> None:
        """Deactivate child flow context safely."""
        if self._scope is not None:
            scope = self._scope
            self._scope = None
            scope.__exit__(None, None, None)

    def succeed(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "completed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Record successful completion of this operation."""
        if self._is_completed and self._completed_event is not None:
            logger.warning(
                "ActiveOperation %s has already been completed; ignoring duplicate success call.",
                self.event_id,
            )
            return self._completed_event

        try:
            if metadata:
                self.metadata.update(metadata)

            if duration_ms is None:
                duration_ms = max(0.0, round((time.perf_counter() - self.start_time) * 1000.0, 2))

            sanitized_meta = sanitize_metadata(
                self.metadata,
                additional_sensitive_keys=getattr(self._recorder, "sensitive_field_keys", ()),
                redact=getattr(self._recorder, "redact_sensitive_fields", True),
            )

            event = FlowEvent(
                event_id=self.event_id,
                flow_id=self.flow_id,
                correlation_id=self.correlation_id,
                parent_event_id=self.parent_event_id,
                source=self.source,
                destination=self.destination,
                action=self.action,
                data_labels=list(self.data_labels),
                metadata=sanitized_meta,
                status=status,
                duration_ms=duration_ms,
            )
            self._recorder.record(event)
            self._is_completed = True
            self._completed_event = event
            return event
        finally:
            self.exit_context()

    def fail(
        self,
        error: Exception | str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "failed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Record failed completion of this operation."""
        if self._is_completed and self._completed_event is not None:
            logger.warning(
                "ActiveOperation %s has already been completed; ignoring duplicate fail call.",
                self.event_id,
            )
            return self._completed_event

        try:
            if metadata:
                self.metadata.update(metadata)

            if isinstance(error, BaseException):
                self.metadata["error"] = type(error).__name__
                self.metadata["error_message"] = str(error)
            else:
                self.metadata["error"] = str(error)

            if duration_ms is None:
                duration_ms = max(0.0, round((time.perf_counter() - self.start_time) * 1000.0, 2))

            sanitized_meta = sanitize_metadata(
                self.metadata,
                additional_sensitive_keys=getattr(self._recorder, "sensitive_field_keys", ()),
                redact=getattr(self._recorder, "redact_sensitive_fields", True),
            )

            event = FlowEvent(
                event_id=self.event_id,
                flow_id=self.flow_id,
                correlation_id=self.correlation_id,
                parent_event_id=self.parent_event_id,
                source=self.source,
                destination=self.destination,
                action=self.action,
                data_labels=list(self.data_labels),
                metadata=sanitized_meta,
                status=status,
                duration_ms=duration_ms,
            )
            self._recorder.record(event)
            self._is_completed = True
            self._completed_event = event
            return event
        finally:
            self.exit_context()

    def __enter__(self) -> ActiveOperation:
        self.enter_context()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        try:
            if exc_val is not None:
                self.fail(error=exc_val)
            elif not self._is_completed:
                self.succeed()
        finally:
            self.exit_context()
        return None

    async def __aenter__(self) -> ActiveOperation:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)


class EventRecorder:
    """Dispatches FlowEvents to an injected transport with lineage and hygiene preservation."""

    def __init__(
        self,
        transport: Transport,
        *,
        strict: bool = False,
        sensitive_field_keys: Sequence[str] = (),
        redact_sensitive_fields: bool = True,
    ) -> None:
        self._transport = transport
        self.strict = strict
        self.sensitive_field_keys = tuple(sensitive_field_keys)
        self.redact_sensitive_fields = redact_sensitive_fields

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

    async def record_async(self, event: FlowEvent) -> None:
        """Asynchronously dispatch a single FlowEvent to the underlying transport."""
        try:
            if hasattr(self._transport, "send_async"):
                await self._transport.send_async(event)
            else:
                import asyncio

                await asyncio.to_thread(self._transport.send, event)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning("EventRecorder failed to asynchronously dispatch event %s: %s", event.event_id, exc)

    async def record_batch_async(self, events: Sequence[FlowEvent]) -> None:
        """Asynchronously dispatch a sequence of FlowEvents to the underlying transport."""
        try:
            if hasattr(self._transport, "send_batch_async"):
                await self._transport.send_batch_async(events)
            else:
                import asyncio

                await asyncio.to_thread(self._transport.send_batch, events)
        except Exception as exc:
            if self.strict:
                raise
            logger.warning("EventRecorder failed to asynchronously dispatch batch of %d events: %s", len(events), exc)

    def flush(self, timeout: float | None = None) -> bool:
        """Flush the underlying transport if it supports flushing.

        Returns True if the flush succeeded or was a no-op, False if it timed out or failed.
        """
        if hasattr(self._transport, "flush"):
            try:
                res = self._transport.flush(timeout=timeout)
                return True if res is None else bool(res)
            except Exception as exc:
                if self.strict:
                    raise
                logger.warning("EventRecorder transport flush failed: %s", exc)
                return False
        return True

    def start_operation(
        self,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef | None = None,
        data_labels: Sequence[str] | str | None = None,
        metadata: dict[str, Any] | None = None,
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
        enter_context: bool = False,
    ) -> ActiveOperation:
        """Start tracking an in-flight operation.

        Associates ambient flow context when identifiers are omitted.
        Can be used procedurally or as a context manager:
            with recorder.start_operation(...) as op:
                ...
        """
        ctx = current_flow_context()
        resolved_flow_id = flow_id or (ctx.flow_id if ctx else uuid4())
        resolved_corr_id = correlation_id if correlation_id is not None else (ctx.correlation_id if ctx else resolved_flow_id)
        resolved_parent_id = parent_event_id if parent_event_id is not None else (ctx.parent_event_id if ctx else None)
        resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
        if resolved_source is None:
            resolved_source = ComponentRef(
                component_id="unknown-source",
                component_type="service",
            )

        resolved_labels = _resolve_data_labels(data_labels)

        op = ActiveOperation(
            recorder=self,
            action=action,
            destination=destination,
            source=resolved_source,
            data_labels=resolved_labels,
            metadata=metadata,
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            event_id=event_id,
        )
        if enter_context:
            op.enter_context()
        return op

    def record_success(
        self,
        operation: ActiveOperation,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "completed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Record successful completion of an active operation."""
        return operation.succeed(
            metadata=metadata,
            status=status,
            duration_ms=duration_ms,
        )

    def record_failure(
        self,
        operation: ActiveOperation,
        error: Exception | str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "failed",
        duration_ms: float | None = None,
    ) -> FlowEvent:
        """Record failed completion of an active operation."""
        return operation.fail(
            error=error,
            metadata=metadata,
            status=status,
            duration_ms=duration_ms,
        )

    def record_transfer(
        self,
        *,
        source: ComponentRef,
        destination: ComponentRef,
        action: str = "data.transfer",
        data_labels: Sequence[str] | str | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        status: str = "observed",
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> FlowEvent:
        """Record a known source-to-destination data transfer event."""
        ctx = current_flow_context()
        resolved_flow_id = flow_id or (ctx.flow_id if ctx else uuid4())
        resolved_corr_id = correlation_id if correlation_id is not None else (ctx.correlation_id if ctx else resolved_flow_id)
        resolved_parent_id = parent_event_id if parent_event_id is not None else (ctx.parent_event_id if ctx else None)

        resolved_labels = _resolve_data_labels(data_labels)
        sanitized_meta = (
            sanitize_metadata(
                metadata or {},
                additional_sensitive_keys=self.sensitive_field_keys,
                redact=self.redact_sensitive_fields,
            )
            if metadata
            else {}
        )

        event = FlowEvent(
            event_id=event_id or uuid4(),
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            source=source,
            destination=destination,
            action=action,
            data_labels=resolved_labels,
            metadata=sanitized_meta,
            status=status,
            duration_ms=duration_ms,
        )
        self.record(event)
        return event

    def record_classified_event(
        self,
        *,
        action: str,
        destination: ComponentRef,
        classification: str | Sequence[str],
        source: ComponentRef | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        status: str = "observed",
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> FlowEvent:
        """Record an event with an explicit data classification."""
        return self.record_operation(
            action=action,
            destination=destination,
            source=source,
            data_labels=classification if not isinstance(classification, str) else [classification],
            metadata=metadata,
            duration_ms=duration_ms,
            status=status,
            flow_id=flow_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            event_id=event_id,
        )

    def record_operation(
        self,
        *,
        action: str,
        destination: ComponentRef,
        source: ComponentRef | None = None,
        duration_ms: float | None = None,
        status: str = "observed",
        data_labels: Sequence[str] | str | None = None,
        metadata: dict[str, Any] | None = None,
        flow_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_event_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> FlowEvent:
        """Construct and record a FlowEvent representing a completed runtime operation.

        Preserves flow_id, correlation_id, and parent_event_id across operations
        using ambient context when not explicitly supplied. Applies data classification
        and sanitization rules.
        """
        ctx = current_flow_context()
        resolved_flow_id = flow_id or (ctx.flow_id if ctx else uuid4())
        resolved_corr_id = correlation_id if correlation_id is not None else (ctx.correlation_id if ctx else resolved_flow_id)
        resolved_parent_id = parent_event_id if parent_event_id is not None else (ctx.parent_event_id if ctx else None)
        resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
        if resolved_source is None:
            resolved_source = ComponentRef(
                component_id="unknown-source",
                component_type="service",
            )

        resolved_labels = _resolve_data_labels(data_labels)
        sanitized_meta = (
            sanitize_metadata(
                metadata or {},
                additional_sensitive_keys=self.sensitive_field_keys,
                redact=self.redact_sensitive_fields,
            )
            if metadata
            else {}
        )

        event = FlowEvent(
            event_id=event_id or uuid4(),
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            source=resolved_source,
            destination=destination,
            action=action,
            data_labels=resolved_labels,
            metadata=sanitized_meta,
            status=status,
            duration_ms=duration_ms,
        )
        self.record(event)
        return event