from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
import functools
import inspect
import logging
from typing import Any, Iterator, TypeVar
from uuid import UUID, uuid4

from amgr_sdk.capture.recorder import ActiveOperation, EventRecorder, _resolve_data_labels
from amgr_sdk.context import current_flow_context
from amgr_sdk.enforcement.client import (
    AuthorizationClient,
    AuthorizationRequest,
)
from amgr_sdk.models.events import ComponentRef, FlowEvent, sanitize_metadata
from amgr_sdk.transport.base import Transport

logger = logging.getLogger("amgr_sdk.capture")

F = TypeVar("F", bound=Callable[..., Any])


class _NoOpTransport(Transport):
    def send(self, event: FlowEvent) -> None:
        pass

    def send_batch(self, events: Sequence[FlowEvent]) -> None:
        pass


def _resolve_recorder(
    client: Any | None = None,
    recorder: EventRecorder | None = None,
) -> EventRecorder | None:
    if recorder is not None:
        return recorder
    if client is not None:
        if isinstance(client, EventRecorder):
            return client
        if hasattr(client, "recorder") and isinstance(client.recorder, EventRecorder):
            return client.recorder
    from amgr_sdk.client import get_default_client

    default_client = get_default_client()
    if default_client is not None and hasattr(default_client, "recorder"):
        return default_client.recorder

    try:
        from amgr_sdk.client import AMGRClient

        return AMGRClient().recorder
    except Exception as exc:
        logger.debug("Could not resolve default EventRecorder: %s", exc)
        return None


def _is_capture_enabled(
    enabled: bool | None,
    client: Any | None,
    recorder: EventRecorder | None,
) -> bool:
    if enabled is not None:
        return enabled
    if client is not None and hasattr(client, "config") and hasattr(client.config, "capture_enabled"):
        return bool(client.config.capture_enabled)
    from amgr_sdk.client import get_default_client

    def_client = get_default_client()
    if def_client is not None and hasattr(def_client, "config") and hasattr(def_client.config, "capture_enabled"):
        return bool(def_client.config.capture_enabled)
    from amgr_sdk.config import AMGRConfig

    try:
        return AMGRConfig.from_env().capture_enabled
    except Exception:
        return True


def _check_authorization(
    rec: EventRecorder,
    authorization_client: AuthorizationClient,
    action: str,
    destination: ComponentRef,
    source: ComponentRef | None,
    data_labels: Sequence[str] | str | None,
    metadata: dict[str, Any] | None,
    raise_transport_errors: bool,
) -> None:
    if rec.application_id is None or rec.environment is None:
        raise ValueError("Authorized event capture requires application_id and environment on EventRecorder.")
    ctx = current_flow_context()
    flow_id = ctx.flow_id if ctx else uuid4()
    corr_id = ctx.correlation_id if ctx else uuid4()
    parent_id = ctx.parent_event_id if ctx else None
    resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
    if resolved_source is None:
        raise ValueError("Event source must be specified or set in the active FlowContext.")

    labels = _resolve_data_labels(data_labels)
    meta = sanitize_metadata(metadata or {}, redact=True)

    auth_req = AuthorizationRequest(
        flow_id=flow_id,
        correlation_id=corr_id,
        subject=resolved_source,
        action=action,
        resource=destination,
        data_labels=labels,
        metadata=meta,
    )
    decision = authorization_client.authorize(auth_req)
    if decision.outcome == "deny":
        blocked_event = FlowEvent(
            application_id=rec.application_id,
            environment=rec.environment,
            flow_id=flow_id,
            correlation_id=corr_id,
            parent_event_id=parent_id,
            source=resolved_source,
            destination=destination,
            action=action,
            data_labels=labels,
            metadata={**meta, "decision_reason": decision.reason},
            status="blocked",
            duration_ms=0.0,
        )
        try:
            rec.record(blocked_event)
        except Exception as exc:
            if raise_transport_errors:
                raise
            logger.warning("Failed to dispatch blocked FlowEvent: %s", exc)
        raise PermissionError(
            f"Operation {action!r} blocked by AMGR policy: {decision.reason}"
        )


def capture(
    action: str,
    *,
    destination: ComponentRef,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "observed",
    raise_transport_errors: bool = False,
) -> Callable[[F], F]:
    """Decorator to instrument functions and trace data flow across components.

    Captures execution duration, maintains hierarchical parent-event causality,
    records failures without swallowing exceptions, and routes through the central recorder.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                rec = _resolve_recorder(client, recorder)
                if not _is_capture_enabled(enabled, client, rec) or rec is None:
                    return await func(*args, **kwargs)

                if authorization_client is not None:
                    _check_authorization(
                        rec=rec,
                        authorization_client=authorization_client,
                        action=action,
                        destination=destination,
                        source=source,
                        data_labels=data_labels,
                        metadata=metadata,
                        raise_transport_errors=raise_transport_errors,
                    )

                original_strict = rec.strict
                if raise_transport_errors:
                    rec.strict = True

                op = rec.start_operation(
                    action=action,
                    destination=destination,
                    source=source,
                    data_labels=data_labels,
                    metadata=metadata,
                    enter_context=True,
                )
                try:
                    result = await func(*args, **kwargs)
                    op.succeed(status=status)
                    return result
                except Exception as exc:
                    op.fail(error=exc, status="failed")
                    raise
                finally:
                    op.exit_context()
                    rec.strict = original_strict

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            rec = _resolve_recorder(client, recorder)
            if not _is_capture_enabled(enabled, client, rec) or rec is None:
                return func(*args, **kwargs)

            if authorization_client is not None:
                _check_authorization(
                    rec=rec,
                    authorization_client=authorization_client,
                    action=action,
                    destination=destination,
                    source=source,
                    data_labels=data_labels,
                    metadata=metadata,
                    raise_transport_errors=raise_transport_errors,
                )

            original_strict = rec.strict
            if raise_transport_errors:
                rec.strict = True

            op = rec.start_operation(
                action=action,
                destination=destination,
                source=source,
                data_labels=data_labels,
                metadata=metadata,
                enter_context=True,
            )
            try:
                result = func(*args, **kwargs)
                op.succeed(status=status)
                return result
            except Exception as exc:
                op.fail(error=exc, status="failed")
                raise
            finally:
                op.exit_context()
                rec.strict = original_strict

        return sync_wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def capture_span(
    action: str,
    *,
    destination: ComponentRef,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "observed",
    raise_transport_errors: bool = False,
) -> Iterator[ActiveOperation]:
    """Context manager for instrumenting code blocks with automatic duration, causality, and failure tracking."""
    rec = _resolve_recorder(client, recorder)
    if not _is_capture_enabled(enabled, client, rec) or rec is None:
        ctx = current_flow_context()
        dummy_source = source or (ctx.current_component if ctx else None)
        dummy_op = ActiveOperation(
            recorder=EventRecorder(_NoOpTransport(), recording_enabled=False),
            action=action,
            destination=destination,
            source=dummy_source,
            data_labels=_resolve_data_labels(data_labels),
            flow_id=uuid4(),
        )
        yield dummy_op
        return

    if authorization_client is not None:
        _check_authorization(
            rec=rec,
            authorization_client=authorization_client,
            action=action,
            destination=destination,
            source=source,
            data_labels=data_labels,
            metadata=metadata,
            raise_transport_errors=raise_transport_errors,
        )

    original_strict = rec.strict
    if raise_transport_errors:
        rec.strict = True

    op = rec.start_operation(
        action=action,
        destination=destination,
        source=source,
        data_labels=data_labels,
        metadata=metadata,
        enter_context=True,
    )
    try:
        yield op
        if not op.is_completed:
            op.succeed(status=status)
    except Exception as exc:
        if not op.is_completed:
            op.fail(error=exc, status="failed")
        raise
    finally:
        op.exit_context()
        rec.strict = original_strict


def capture_agent(
    agent: ComponentRef | str,
    *,
    action: str = "agent.invoke",
    name: str | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "completed",
    raise_transport_errors: bool = False,
) -> Callable[[F], F]:
    """Decorator to instrument explicitly selected AI agent operations (e.g. invoke, delegate, plan)."""
    destination = (
        ComponentRef.agent(component_id=agent, name=name or agent)
        if isinstance(agent, str)
        else agent
    )
    labels = ["agent"] if data_labels is None else data_labels
    return capture(
        action=action,
        destination=destination,
        source=source,
        data_labels=labels,
        metadata=metadata,
        client=client,
        recorder=recorder,
        authorization_client=authorization_client,
        enabled=enabled,
        status=status,
        raise_transport_errors=raise_transport_errors,
    )


def capture_tool(
    tool: ComponentRef | str,
    *,
    action: str = "tool.execute",
    name: str | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "completed",
    raise_transport_errors: bool = False,
) -> Callable[[F], F]:
    """Decorator to instrument explicitly selected tool operations (e.g. execute, search, query)."""
    destination = (
        ComponentRef.tool(component_id=tool, name=name or tool)
        if isinstance(tool, str)
        else tool
    )
    labels = ["tool"] if data_labels is None else data_labels
    return capture(
        action=action,
        destination=destination,
        source=source,
        data_labels=labels,
        metadata=metadata,
        client=client,
        recorder=recorder,
        authorization_client=authorization_client,
        enabled=enabled,
        status=status,
        raise_transport_errors=raise_transport_errors,
    )


@contextmanager
def agent_span(
    agent: ComponentRef | str,
    *,
    action: str = "agent.invoke",
    name: str | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "completed",
    raise_transport_errors: bool = False,
) -> Iterator[ActiveOperation]:
    """Context manager for instrumenting an agent execution block with automatic lineage and duration."""
    destination = (
        ComponentRef.agent(component_id=agent, name=name or agent)
        if isinstance(agent, str)
        else agent
    )
    labels = ["agent"] if data_labels is None else data_labels
    with capture_span(
        action=action,
        destination=destination,
        source=source,
        data_labels=labels,
        metadata=metadata,
        client=client,
        recorder=recorder,
        authorization_client=authorization_client,
        enabled=enabled,
        status=status,
        raise_transport_errors=raise_transport_errors,
    ) as op:
        yield op


@contextmanager
def tool_span(
    tool: ComponentRef | str,
    *,
    action: str = "tool.execute",
    name: str | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    status: str = "completed",
    raise_transport_errors: bool = False,
) -> Iterator[ActiveOperation]:
    """Context manager for instrumenting a tool execution block with automatic lineage and duration."""
    destination = (
        ComponentRef.tool(component_id=tool, name=name or tool)
        if isinstance(tool, str)
        else tool
    )
    labels = ["tool"] if data_labels is None else data_labels
    with capture_span(
        action=action,
        destination=destination,
        source=source,
        data_labels=labels,
        metadata=metadata,
        client=client,
        recorder=recorder,
        authorization_client=authorization_client,
        enabled=enabled,
        status=status,
        raise_transport_errors=raise_transport_errors,
    ) as op:
        yield op
