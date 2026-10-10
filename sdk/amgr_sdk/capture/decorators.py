from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def capture(action: str) -> Callable[[F], F]:
    """Reserved decorator API; automatic function instrumentation is not implemented."""

    def decorate(function: F) -> F:
        raise NotImplementedError(
            f"Capture decorator for action {action!r} is not implemented."
        )

    return decorate