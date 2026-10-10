from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator
from uuid import UUID, uuid4


@dataclass(frozen=True)
class FlowContext:
    flow_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)


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


def flow_context(context: FlowContext | None = None) -> _FlowContextScope:
    return _FlowContextScope(context or FlowContext())