from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from amgr_sdk.models.events import ComponentRef


@dataclass(frozen=True)
class FlowContext:
    flow_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    parent_event_id: UUID | None = None
    current_component: ComponentRef | None = None

    def child_context(
        self,
        parent_event_id: UUID | None = None,
        component: ComponentRef | None = None,
    ) -> FlowContext:
        """Create a child context inheriting flow_id and correlation_id."""
        return FlowContext(
            flow_id=self.flow_id,
            correlation_id=self.correlation_id,
            parent_event_id=parent_event_id if parent_event_id is not None else self.parent_event_id,
            current_component=component if component is not None else self.current_component,
        )

    def __enter__(self) -> FlowContext:
        token = _current_context.set(self)
        object.__setattr__(self, "_token", token)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        token = getattr(self, "_token", None)
        if token is not None:
            _current_context.reset(token)


_current_context: ContextVar[FlowContext | None] = ContextVar("amgr_flow_context", default=None)


def current_flow_context() -> FlowContext | None:
    return _current_context.get()


class _FlowContextScope:
    def __init__(self, context: FlowContext) -> None:
        self.context = context
        self._token: Token[FlowContext | None] | None = None

    def __enter__(self) -> FlowContext:
        self._token = _current_context.set(self.context)
        return self.context

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._token is not None:
            _current_context.reset(self._token)


def flow_context(
    context: FlowContext | None = None,
    *,
    parent_event_id: UUID | None = None,
    component: ComponentRef | None = None,
) -> _FlowContextScope:
    if context is None:
        active = current_flow_context()
        if active is not None:
            context = active.child_context(
                parent_event_id=parent_event_id,
                component=component,
            )
        else:
            context = FlowContext(
                parent_event_id=parent_event_id,
                current_component=component,
            )
    elif parent_event_id is not None or component is not None:
        context = context.child_context(
            parent_event_id=parent_event_id,
            component=component,
        )
    return _FlowContextScope(context)