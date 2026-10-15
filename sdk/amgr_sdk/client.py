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
        *,
        buffered: bool | None = None,
        auto_start: bool = True,
        authorization_client: Any | None = None,
    ) -> None:
        self.config = config or AMGRConfig.from_env()
        # Synchronous direct delivery is the default unless explicitly opted in
        self._is_buffered: bool = self.config.buffered if buffered is None else bool(buffered)

        if authorization_client is not None:
            self._authorization_client = authorization_client
        else:
            from amgr_sdk.enforcement.client import HttpAuthorizationClient

            self._authorization_client = HttpAuthorizationClient(self.config)

        if recorder is None:
            from amgr_sdk.transport.http import HttpTransport

            base_transport = HttpTransport(self.config)
            if self._is_buffered:
                from amgr_sdk.delivery import DeliveryPipeline, QueuedTransport

                self._pipeline: Any = DeliveryPipeline(base_transport, config=self.config)
                if auto_start:
                    self._pipeline.start()
                transport = QueuedTransport(self._pipeline)
            else:
                self._pipeline = None
                transport = base_transport

            self._recorder = EventRecorder(
                transport,
                sensitive_field_keys=self.config.sensitive_field_keys,
                redact_sensitive_fields=self.config.redact_sensitive_fields,
            )
        else:
            from amgr_sdk.delivery import DeliveryPipeline, QueuedTransport

            if isinstance(recorder.transport, QueuedTransport):
                self._recorder = recorder
                self._pipeline = recorder.transport.pipeline
                self._is_buffered = True
                if auto_start and not self._pipeline.is_running:
                    self._pipeline.start()
            elif self._is_buffered:
                self._pipeline = DeliveryPipeline(recorder.transport, config=self.config)
                if auto_start:
                    self._pipeline.start()
                self._recorder = EventRecorder(
                    QueuedTransport(self._pipeline),
                    strict=recorder.strict,
                    sensitive_field_keys=self.config.sensitive_field_keys,
                    redact_sensitive_fields=self.config.redact_sensitive_fields,
                )
            else:
                self._recorder = recorder
                self._pipeline = None

    @classmethod
    def buffered(
        cls,
        config: AMGRConfig | None = None,
        *,
        recorder: EventRecorder | None = None,
        auto_start: bool = True,
        authorization_client: Any | None = None,
    ) -> "AMGRClient":
        """Factory creating an AMGRClient explicitly configured with buffered asynchronous delivery."""
        return cls(
            recorder=recorder,
            config=config,
            buffered=True,
            auto_start=auto_start,
            authorization_client=authorization_client,
        )

    @property
    def recorder(self) -> EventRecorder:
        return self._recorder

    @property
    def authorization_client(self) -> Any:
        return self._authorization_client

    def record(self, event: FlowEvent) -> None:
        if not self.config.capture_enabled:
            return
        self._recorder.record(event)

    def record_batch(self, events: Sequence[FlowEvent]) -> None:
        if not self.config.capture_enabled:
            return
        self._recorder.record_batch(events)

    async def record_async(self, event: FlowEvent) -> None:
        if not self.config.capture_enabled:
            return
        await self._recorder.record_async(event)

    async def record_batch_async(self, events: Sequence[FlowEvent]) -> None:
        if not self.config.capture_enabled:
            return
        await self._recorder.record_batch_async(events)

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

    def flow(
        self,
        source: ComponentRef | str | None = None,
        *,
        flow_id: UUID | str | None = None,
        correlation_id: UUID | str | None = None,
        parent_event_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
        nested: bool = False,
    ) -> Any:
        """Establish an explicit workflow flow context bound to this client."""
        from amgr_sdk.context import flow

        return flow(
            source=source,
            flow_id=flow_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            metadata=metadata,
            nested=nested,
        )

    def export_context(
        self,
        context: Any = None,
        *,
        as_headers: bool = False,
    ) -> dict[str, Any]:
        """Export the active or given context into a serializable carrier."""
        from amgr_sdk.context import export_context

        return export_context(context=context, as_headers=as_headers)

    def import_context(
        self,
        carrier: Any,
        *,
        source: ComponentRef | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Establish and restore an execution context from an imported carrier."""
        from amgr_sdk.context import import_context

        return import_context(carrier, source=source, metadata=metadata)

    def restore_context(
        self,
        carrier: Any,
        *,
        source: ComponentRef | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Alias for import_context."""
        return self.import_context(carrier, source=source, metadata=metadata)

    @property
    def is_buffered(self) -> bool:
        """Return True if buffered asynchronous delivery is enabled for this client."""
        return self._pipeline is not None

    @property
    def is_running(self) -> bool:
        """Return True if the background delivery pipeline worker is currently active."""
        if self._pipeline is not None:
            return self._pipeline.is_running
        return False

    @property
    def pipeline(self) -> Any:
        """The delivery pipeline instance if queueing is enabled, or None."""
        return self._pipeline

    @property
    def metrics(self) -> Any:
        """Delivery metrics counter for the pipeline, or None if queueing is disabled."""
        if self._pipeline is not None:
            return self._pipeline.metrics
        return None

    def start(self) -> None:
        """Start the background delivery pipeline if buffered mode is enabled."""
        if self._pipeline is not None:
            self._pipeline.start()

    def flush(self, timeout: float | None = None) -> bool:
        """Flush queued events through the transport within the given timeout seconds.

        Returns True if all events were delivered, False if timeout expired or flush failed.
        """
        if self._pipeline is not None:
            return self._pipeline.flush(timeout=timeout)
        return self._recorder.flush(timeout=timeout)

    def close(self, timeout: float | None = None) -> int:
        """Gracefully close the client, stopping workers and flushing queued events.

        Returns the number of undelivered events remaining (0 on clean shutdown).
        """
        if self._pipeline is not None:
            return self._pipeline.stop(timeout=timeout)
        self._recorder.flush(timeout=timeout)
        return 0

    def __enter__(self) -> "AMGRClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

