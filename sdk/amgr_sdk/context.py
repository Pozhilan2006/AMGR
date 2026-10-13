from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from amgr_sdk.models.events import ComponentRef

# Standard HTTP and message carrier header keys for AMGR context propagation.
HEADER_FLOW_ID: str = "X-AMGR-Flow-ID"
HEADER_CORRELATION_ID: str = "X-AMGR-Correlation-ID"
HEADER_PARENT_EVENT_ID: str = "X-AMGR-Parent-Event-ID"


def _coerce_uuid(val: UUID | str | None) -> UUID | None:
    """Coerce a UUID or string identifier to a UUID instance.

    Returns None if val is None or empty. If a non-UUID string is provided,
    deterministically derives a UUID5 to support arbitrary upstream correlation
    identifiers within the UUID event contract.
    """
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None
        try:
            return UUID(cleaned)
        except ValueError:
            from uuid import NAMESPACE_URL, uuid5

            return uuid5(NAMESPACE_URL, cleaned)
    raise TypeError(f"Expected UUID or string identifier, got {type(val).__name__}")


@dataclass(frozen=True)
class FlowContext:
    """Immutable execution context holding correlation and lineage identifiers.

    Attributes:
        flow_id: Logical workflow or distributed transaction identifier.
        correlation_id: Correlated request, session, or execution identifier.
            Defaults to flow_id if not explicitly provided.
        parent_event_id: Immediate parent event identifier in the causal chain when known.
            Must be None for root operations; never fabricated.
        current_component: Descriptor of the active component executing in this scope.
        metadata: Safe contextual metadata. Centralized sanitization is applied.
    """

    flow_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default=None)  # type: ignore[assignment]
    parent_event_id: UUID | None = None
    current_component: ComponentRef | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce flow_id
        fid = _coerce_uuid(self.flow_id) or uuid4()
        object.__setattr__(self, "flow_id", fid)

        # Coerce correlation_id, default to flow_id if None
        cid = _coerce_uuid(self.correlation_id) if self.correlation_id is not None else fid
        object.__setattr__(self, "correlation_id", cid)

        # Coerce parent_event_id
        pid = _coerce_uuid(self.parent_event_id) if self.parent_event_id is not None else None
        object.__setattr__(self, "parent_event_id", pid)

        # Sanitize metadata if provided
        from amgr_sdk.models.events import sanitize_metadata

        clean_meta = (
            sanitize_metadata(self.metadata, redact=True)
            if self.metadata and isinstance(self.metadata, dict)
            else {}
        )
        object.__setattr__(self, "metadata", clean_meta)

    def child_context(
        self,
        parent_event_id: UUID | str | None = None,
        component: ComponentRef | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FlowContext:
        """Create a child context inheriting flow_id and correlation_id.

        Sets parent_event_id to the specified initiating event and updates
        component identity and metadata if provided.
        """
        new_meta = dict(self.metadata)
        if metadata:
            new_meta.update(metadata)
        return FlowContext(
            flow_id=self.flow_id,
            correlation_id=self.correlation_id,
            parent_event_id=parent_event_id if parent_event_id is not None else self.parent_event_id,
            current_component=component if component is not None else self.current_component,
            metadata=new_meta,
        )

    def to_carrier(self) -> dict[str, str]:
        """Serialize correlation and lineage identifiers into HTTP/message headers."""
        carrier: dict[str, str] = {
            HEADER_FLOW_ID: str(self.flow_id),
            HEADER_CORRELATION_ID: str(self.correlation_id),
        }
        if self.parent_event_id is not None:
            carrier[HEADER_PARENT_EVENT_ID] = str(self.parent_event_id)
        return carrier

    @classmethod
    def from_carrier(cls, carrier: Mapping[str, str] | None) -> FlowContext | None:
        """Extract a FlowContext from HTTP/message headers case-insensitively.

        Returns None if no AMGR correlation headers are present.
        """
        if not carrier:
            return None

        # Build case-insensitive lookup
        lookup = {k.lower(): str(v).strip() for k, v in carrier.items() if v is not None}
        flow_id_str = lookup.get("x-amgr-flow-id") or lookup.get("x-flow-id")
        if not flow_id_str:
            return None

        try:
            flow_id = UUID(flow_id_str)
        except (ValueError, TypeError):
            return None

        corr_id_str = lookup.get("x-amgr-correlation-id") or lookup.get("x-correlation-id")
        try:
            correlation_id = UUID(corr_id_str) if corr_id_str else flow_id
        except (ValueError, TypeError):
            correlation_id = flow_id

        parent_id_str = lookup.get("x-amgr-parent-event-id") or lookup.get("x-parent-event-id")
        try:
            parent_event_id = UUID(parent_id_str) if parent_id_str else None
        except (ValueError, TypeError):
            parent_event_id = None

        return cls(
            flow_id=flow_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to a dictionary suitable for structured logging or message payloads."""
        data: dict[str, Any] = {
            "flow_id": str(self.flow_id),
            "correlation_id": str(self.correlation_id),
        }
        if self.parent_event_id is not None:
            data["parent_event_id"] = str(self.parent_event_id)
        if self.current_component is not None:
            data["current_component"] = self.current_component.model_dump(mode="json")
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> FlowContext | None:
        """Construct a FlowContext from a serialized dictionary payload."""
        if not data:
            return None

        from amgr_sdk.models.events import ComponentRef

        fid = _coerce_uuid(data.get("flow_id"))
        if fid is None:
            return None

        cid = _coerce_uuid(data.get("correlation_id")) or fid
        pid = _coerce_uuid(data.get("parent_event_id"))

        comp_data = data.get("current_component") or data.get("component")
        comp = None
        if comp_data and isinstance(comp_data, dict):
            try:
                comp = ComponentRef.model_validate(comp_data)
            except Exception:
                comp = None

        meta = data.get("metadata")
        clean_meta = dict(meta) if meta and isinstance(meta, dict) else {}

        return cls(
            flow_id=fid,
            correlation_id=cid,
            parent_event_id=pid,
            current_component=comp,
            metadata=clean_meta,
        )

    @property
    def initiating_component(self) -> ComponentRef | None:
        """Descriptor of the flow's initiating or active component."""
        return self.current_component

    def __enter__(self) -> FlowContext:
        token = _current_context.set(self)
        object.__setattr__(self, "_token", token)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        token = getattr(self, "_token", None)
        if token is not None:
            _current_context.reset(token)

    async def __aenter__(self) -> FlowContext:
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.__exit__(exc_type, exc_value, traceback)


_current_context: ContextVar[FlowContext | None] = ContextVar("amgr_flow_context", default=None)


def current_flow_context() -> FlowContext | None:
    """Retrieve the active ambient FlowContext, or None if no context is active."""
    return _current_context.get()


def set_flow_context(context: FlowContext | None) -> Token[FlowContext | None]:
    """Set the active ambient flow context and return the Token for reset."""
    return _current_context.set(context)


def reset_flow_context(token: Token[FlowContext | None]) -> None:
    """Reset the active ambient flow context to its prior state."""
    _current_context.reset(token)


def inject_context(carrier: dict[str, str], context: FlowContext | None = None) -> dict[str, str]:
    """Inject AMGR correlation headers into a mutable dictionary (e.g. HTTP headers).

    If context is not supplied, injects the ambient current_flow_context().
    Returns the updated carrier dictionary.
    """
    ctx = context or current_flow_context()
    if ctx is not None:
        carrier.update(ctx.to_carrier())
    return carrier


def extract_context(carrier: Mapping[str, Any] | FlowContext | None) -> FlowContext | None:
    """Extract a FlowContext from HTTP headers, message payloads, or structured context dictionaries.

    Supports:
    - FlowContext instances directly.
    - Structured dictionary payloads (containing 'flow_id', 'correlation_id', etc.).
    - HTTP header carriers (containing 'X-AMGR-Flow-ID', etc., case-insensitively).

    Returns None if carrier is missing or contains no valid AMGR flow identifiers.
    Never fabricates a parent_event_id if one is not present.
    """
    if carrier is None:
        return None
    if isinstance(carrier, FlowContext):
        return carrier
    if not isinstance(carrier, Mapping):
        return None

    # Check for structured dictionary format
    if "flow_id" in carrier:
        return FlowContext.from_dict(carrier)

    # Check for header carrier format
    return FlowContext.from_carrier(carrier)


class _FlowContextScope:
    """Manages context entry and restoration for sync and async execution scopes."""

    def __init__(self, context: FlowContext) -> None:
        self.context = context
        self._token: Token[FlowContext | None] | None = None

    def __enter__(self) -> FlowContext:
        self._token = _current_context.set(self.context)
        return self.context

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._token is not None:
            _current_context.reset(self._token)
            self._token = None

    async def __aenter__(self) -> FlowContext:
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.__exit__(exc_type, exc_value, traceback)

    def __call__(self, func: Any) -> Any:
        """Allow using flow context as a function decorator."""
        import functools
        import inspect

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with self:
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with self:
                return func(*args, **kwargs)

        return sync_wrapper


def _coerce_component(component: ComponentRef | str | None) -> ComponentRef | None:
    """Coerce string component identifiers to ComponentRef."""
    if component is None:
        return None
    from amgr_sdk.models.events import ComponentRef

    if isinstance(component, ComponentRef):
        return component
    if isinstance(component, str) and component.strip():
        s = component.strip()
        comp_type = "agent" if "agent" in s.lower() else "service"
        return ComponentRef(component_id=s, component_type=comp_type, name=s)
    return None


def flow(
    source: ComponentRef | str | None = None,
    *,
    flow_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
    parent_event_id: UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
    nested: bool = False,
) -> _FlowContextScope:
    """Establish an explicit workflow flow boundary.

    Can be used as a synchronous context manager, asynchronous context manager,
    or decorator:

        with flow(source="customer_service_agent"):
            ...

        async with flow(source="customer_service_agent"):
            ...

        @flow(source="customer_service_agent")
        def run_task():
            ...

    Args:
        source: Initiating component identifier or ComponentRef descriptor.
        flow_id: Explicit workflow identifier. If omitted and nested=False,
            a new flow_id UUID is generated. If nested=True and an active
            context exists, the active flow_id is reused.
        correlation_id: Optional upstream correlation identifier. If omitted,
            inherits from active context or defaults to flow_id.
        parent_event_id: Optional immediate parent event identifier.
        metadata: Optional contextual metadata (sanitized via centralized rules).
        nested: If True, nests within the current flow (inheriting flow_id).
            If False (default), establishes a distinct, independent logical flow.

    Returns:
        A _FlowContextScope managing context entry and safe restoration.
    """
    comp = _coerce_component(source)
    active = current_flow_context()

    if nested and active is not None:
        # Nesting inside existing workflow
        resolved_flow_id = _coerce_uuid(flow_id) or active.flow_id
        resolved_corr_id = _coerce_uuid(correlation_id) or active.correlation_id
        resolved_parent_id = _coerce_uuid(parent_event_id) or active.parent_event_id
        resolved_comp = comp or active.current_component
        merged_meta = dict(active.metadata)
        if metadata:
            merged_meta.update(metadata)
        ctx = FlowContext(
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            current_component=resolved_comp,
            metadata=merged_meta,
        )
    else:
        # Establishing a new, independent workflow boundary
        resolved_flow_id = _coerce_uuid(flow_id) or uuid4()
        resolved_corr_id = _coerce_uuid(correlation_id) or (active.correlation_id if active else resolved_flow_id)
        resolved_parent_id = _coerce_uuid(parent_event_id)
        ctx = FlowContext(
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            current_component=comp,
            metadata=metadata or {},
        )

    return _FlowContextScope(ctx)


def flow_context(
    context: FlowContext | None = None,
    *,
    flow_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
    parent_event_id: UUID | str | None = None,
    component: ComponentRef | None = None,
    metadata: dict[str, Any] | None = None,
) -> _FlowContextScope:
    """Context manager for establishing or nesting ambient flow context.

    Supports both synchronous ('with flow_context():') and asynchronous
    ('async with flow_context():') scopes.
    """
    if context is None:
        active = current_flow_context()
        if active is not None:
            context = active.child_context(
                parent_event_id=parent_event_id,
                component=component,
                metadata=metadata,
            )
            if flow_id is not None or correlation_id is not None:
                context = FlowContext(
                    flow_id=flow_id if flow_id is not None else context.flow_id,
                    correlation_id=correlation_id if correlation_id is not None else context.correlation_id,
                    parent_event_id=context.parent_event_id,
                    current_component=context.current_component,
                    metadata=context.metadata,
                )
        else:
            context = FlowContext(
                flow_id=flow_id,  # type: ignore[arg-type]
                correlation_id=correlation_id,  # type: ignore[arg-type]
                parent_event_id=parent_event_id,  # type: ignore[arg-type]
                current_component=component,
                metadata=metadata or {},
            )
    else:
        if (
            parent_event_id is not None
            or component is not None
            or metadata is not None
            or flow_id is not None
            or correlation_id is not None
        ):
            context = context.child_context(
                parent_event_id=parent_event_id,
                component=component,
                metadata=metadata,
            )
            if flow_id is not None or correlation_id is not None:
                context = FlowContext(
                    flow_id=flow_id if flow_id is not None else context.flow_id,
                    correlation_id=correlation_id if correlation_id is not None else context.correlation_id,
                    parent_event_id=context.parent_event_id,
                    current_component=context.current_component,
                    metadata=context.metadata,
                )
    return _FlowContextScope(context)

def export_context(
    context: FlowContext | None = None,
    *,
    as_headers: bool = False,
) -> dict[str, Any]:
    """Export the active or specified FlowContext into a safe, serializable carrier.

    Args:
        context: Optional FlowContext to export. If None, the ambient context is used.
        as_headers: If True, exports HTTP/message headers (string values).
                    If False (default), exports a structured dictionary suitable for
                    JSON serialization, message queue payloads, or background tasks.

    Returns:
        A serializable dictionary containing safe context identifiers (flow_id, correlation_id,
        and parent_event_id when known). Returns an empty dictionary if no context is active.
    """
    ctx = context or current_flow_context()
    if ctx is None:
        return {}
    if as_headers:
        return ctx.to_carrier()
    return ctx.to_dict()


def import_context(
    carrier: Mapping[str, Any] | FlowContext | None,
    *,
    source: ComponentRef | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> _FlowContextScope:
    """Establish and restore an execution context from an imported carrier.

    Accepts HTTP headers, message payloads, or structured context dictionaries.
    Preserves valid upstream flow_id and correlation_id without fabricating
    missing parent IDs.

    Args:
        carrier: Serialized carrier or FlowContext to restore.
        source: Optional component identity overriding or setting the current component.
        metadata: Optional additional safe metadata to merge into the restored context.

    Returns:
        A context manager (_FlowContextScope) supporting sync (`with`), async (`async with`),
        and decorator usage, safely restoring prior context upon normal exit or exception.
    """
    ctx = extract_context(carrier)
    if ctx is None:
        active = current_flow_context()
        if active is not None:
            ctx = active
        else:
            ctx = FlowContext()

    if source is not None or metadata:
        comp = _coerce_component(source) if source is not None else ctx.current_component
        new_meta = dict(ctx.metadata)
        if metadata:
            new_meta.update(metadata)
        ctx = FlowContext(
            flow_id=ctx.flow_id,
            correlation_id=ctx.correlation_id,
            parent_event_id=ctx.parent_event_id,
            current_component=comp,
            metadata=new_meta,
        )

    return _FlowContextScope(ctx)


restore_context = import_context
