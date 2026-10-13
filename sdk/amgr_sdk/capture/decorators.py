from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
import functools
import inspect
import logging
import time
from typing import Any, Iterator, TypeVar
from uuid import UUID, uuid4

from amgr_sdk.context import current_flow_context, flow_context
from amgr_sdk.enforcement.client import (
    AuthorizationClient,
    AuthorizationRequest,
)
from amgr_sdk.models.events import ComponentRef, FlowEvent

logger = logging.getLogger("amgr_sdk.capture")

F = TypeVar("F", bound=Callable[..., Any])


def _resolve_client(client: Any | None) -> Any | None:
    if client is not None:
        return client
    from amgr_sdk.client import get_default_client

    return get_default_client()


def capture(
    action: str,
    *,
    destination: ComponentRef,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    raise_transport_errors: bool = False,
) -> Callable[[F], F]:
    """Decorator to instrument functions and trace data flow across components.

    Captures execution duration, maintains hierarchical parent-event causality,
    and optionally evaluates pre-operation authorization hooks.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                active_client = _resolve_client(client)
                is_enabled = (
                    enabled
                    if enabled is not None
                    else (
                        getattr(active_client.config, "capture_enabled", True)
                        if active_client and hasattr(active_client, "config")
                        else True
                    )
                )
                if not is_enabled:
                    return await func(*args, **kwargs)

                ctx = current_flow_context()
                resolved_flow_id = ctx.flow_id if ctx else uuid4()
                resolved_corr_id = ctx.correlation_id if ctx else uuid4()
                resolved_parent_id = ctx.parent_event_id if ctx else None
                resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
                if resolved_source is None:
                    resolved_source = ComponentRef(
                        component_id="unknown-source",
                        component_type="service",
                    )

                event_id = uuid4()
                labels = list(data_labels) if data_labels else []

                from amgr_sdk.models.events import sanitize_metadata

                cfg = getattr(active_client, "config", None) if active_client else None
                sens_keys = getattr(cfg, "sensitive_field_keys", ()) if cfg else ()
                redact = getattr(cfg, "redact_sensitive_fields", True) if cfg else True
                meta = (
                    sanitize_metadata(
                        metadata,
                        additional_sensitive_keys=sens_keys,
                        redact=redact,
                    )
                    if metadata
                    else {}
                )

                # Pre-operation enforcement guard
                if authorization_client is not None:
                    auth_req = AuthorizationRequest(
                        flow_id=resolved_flow_id,
                        correlation_id=resolved_corr_id,
                        subject=resolved_source,
                        action=action,
                        resource=destination,
                        data_labels=labels,
                        metadata=meta,
                    )
                    decision = authorization_client.authorize(auth_req)
                    if decision.outcome == "deny":
                        blocked_event = FlowEvent(
                            event_id=event_id,
                            flow_id=resolved_flow_id,
                            correlation_id=resolved_corr_id,
                            parent_event_id=resolved_parent_id,
                            source=resolved_source,
                            destination=destination,
                            action=action,
                            data_labels=labels,
                            metadata={**meta, "decision_reason": decision.reason},
                            status="blocked",
                            duration_ms=0.0,
                        )
                        _record_safely(blocked_event, client, raise_transport_errors)
                        raise PermissionError(
                            f"Operation {action!r} blocked by AMGR policy: {decision.reason}"
                        )

                start_time = time.perf_counter()
                status = "observed"
                with flow_context(
                    parent_event_id=event_id,
                    component=destination,
                ):
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except Exception as exc:
                        status = "failed"
                        meta["error"] = type(exc).__name__
                        raise
                    finally:
                        try:
                            duration_ms = (time.perf_counter() - start_time) * 1000.0
                            event = FlowEvent(
                                event_id=event_id,
                                flow_id=resolved_flow_id,
                                correlation_id=resolved_corr_id,
                                parent_event_id=resolved_parent_id,
                                source=resolved_source,
                                destination=destination,
                                action=action,
                                data_labels=labels,
                                metadata=meta,
                                status=status,
                                duration_ms=round(duration_ms, 2),
                            )
                            _record_safely(event, client, raise_transport_errors)
                        except Exception as capture_exc:
                            if raise_transport_errors:
                                raise
                            logger.warning("AMGR telemetry capture failed: %s", capture_exc)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            active_client = _resolve_client(client)
            is_enabled = (
                enabled
                if enabled is not None
                else (
                    getattr(active_client.config, "capture_enabled", True)
                    if active_client and hasattr(active_client, "config")
                    else True
                )
            )
            if not is_enabled:
                return func(*args, **kwargs)

            ctx = current_flow_context()
            resolved_flow_id = ctx.flow_id if ctx else uuid4()
            resolved_corr_id = ctx.correlation_id if ctx else uuid4()
            resolved_parent_id = ctx.parent_event_id if ctx else None
            resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
            if resolved_source is None:
                resolved_source = ComponentRef(
                    component_id="unknown-source",
                    component_type="service",
                )

            event_id = uuid4()
            labels = list(data_labels) if data_labels else []

            from amgr_sdk.models.events import sanitize_metadata

            cfg = getattr(active_client, "config", None) if active_client else None
            sens_keys = getattr(cfg, "sensitive_field_keys", ()) if cfg else ()
            redact = getattr(cfg, "redact_sensitive_fields", True) if cfg else True
            meta = (
                sanitize_metadata(
                    metadata,
                    additional_sensitive_keys=sens_keys,
                    redact=redact,
                )
                if metadata
                else {}
            )

            # Pre-operation enforcement guard
            if authorization_client is not None:
                auth_req = AuthorizationRequest(
                    flow_id=resolved_flow_id,
                    correlation_id=resolved_corr_id,
                    subject=resolved_source,
                    action=action,
                    resource=destination,
                    data_labels=labels,
                    metadata=meta,
                )
                decision = authorization_client.authorize(auth_req)
                if decision.outcome == "deny":
                    blocked_event = FlowEvent(
                        event_id=event_id,
                        flow_id=resolved_flow_id,
                        correlation_id=resolved_corr_id,
                        parent_event_id=resolved_parent_id,
                        source=resolved_source,
                        destination=destination,
                        action=action,
                        data_labels=labels,
                        metadata={**meta, "decision_reason": decision.reason},
                        status="blocked",
                        duration_ms=0.0,
                    )
                    _record_safely(blocked_event, client, raise_transport_errors)
                    raise PermissionError(
                        f"Operation {action!r} blocked by AMGR policy: {decision.reason}"
                    )

            start_time = time.perf_counter()
            status = "observed"
            with flow_context(
                parent_event_id=event_id,
                component=destination,
            ):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as exc:
                    status = "failed"
                    meta["error"] = type(exc).__name__
                    raise
                finally:
                    try:
                        duration_ms = (time.perf_counter() - start_time) * 1000.0
                        event = FlowEvent(
                            event_id=event_id,
                            flow_id=resolved_flow_id,
                            correlation_id=resolved_corr_id,
                            parent_event_id=resolved_parent_id,
                            source=resolved_source,
                            destination=destination,
                            action=action,
                            data_labels=labels,
                            metadata=meta,
                            status=status,
                            duration_ms=round(duration_ms, 2),
                        )
                        _record_safely(event, client, raise_transport_errors)
                    except Exception as capture_exc:
                        if raise_transport_errors:
                            raise
                        logger.warning("AMGR telemetry capture failed: %s", capture_exc)

        return sync_wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def capture_span(
    action: str,
    *,
    destination: ComponentRef,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    authorization_client: AuthorizationClient | None = None,
    enabled: bool | None = None,
    raise_transport_errors: bool = False,
) -> Iterator[FlowEvent]:
    """Context manager for instrumenting code blocks with automatic duration and causality."""
    active_client = _resolve_client(client)
    is_enabled = (
        enabled
        if enabled is not None
        else (
            getattr(active_client.config, "capture_enabled", True)
            if active_client and hasattr(active_client, "config")
            else True
        )
    )
    if not is_enabled:
        dummy_event = FlowEvent(
            flow_id=uuid4(),
            source=source or ComponentRef(component_id="unknown-source", component_type="service"),
            destination=destination,
            action=action,
        )
        yield dummy_event
        return

    ctx = current_flow_context()
    resolved_flow_id = ctx.flow_id if ctx else uuid4()
    resolved_corr_id = ctx.correlation_id if ctx else uuid4()
    resolved_parent_id = ctx.parent_event_id if ctx else None
    resolved_source = source or (ctx.current_component if ctx and ctx.current_component else None)
    if resolved_source is None:
        resolved_source = ComponentRef(
            component_id="unknown-source",
            component_type="service",
        )

    event_id = uuid4()
    labels = list(data_labels) if data_labels else []

    from amgr_sdk.models.events import sanitize_metadata

    cfg = getattr(active_client, "config", None) if active_client else None
    sens_keys = getattr(cfg, "sensitive_field_keys", ()) if cfg else ()
    redact = getattr(cfg, "redact_sensitive_fields", True) if cfg else True
    meta = (
        sanitize_metadata(
            metadata,
            additional_sensitive_keys=sens_keys,
            redact=redact,
        )
        if metadata
        else {}
    )

    if authorization_client is not None:
        auth_req = AuthorizationRequest(
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            subject=resolved_source,
            action=action,
            resource=destination,
            data_labels=labels,
            metadata=meta,
        )
        decision = authorization_client.authorize(auth_req)
        if decision.outcome == "deny":
            blocked_event = FlowEvent(
                event_id=event_id,
                flow_id=resolved_flow_id,
                correlation_id=resolved_corr_id,
                parent_event_id=resolved_parent_id,
                source=resolved_source,
                destination=destination,
                action=action,
                data_labels=labels,
                metadata={**meta, "decision_reason": decision.reason},
                status="blocked",
                duration_ms=0.0,
            )
            _record_safely(blocked_event, client, raise_transport_errors)
            raise PermissionError(
                f"Operation {action!r} blocked by AMGR policy: {decision.reason}"
            )

    start_time = time.perf_counter()
    status = "observed"
    with flow_context(parent_event_id=event_id, component=destination):
        placeholder_event = FlowEvent(
            event_id=event_id,
            flow_id=resolved_flow_id,
            correlation_id=resolved_corr_id,
            parent_event_id=resolved_parent_id,
            source=resolved_source,
            destination=destination,
            action=action,
            data_labels=labels,
            metadata=meta,
            status=status,
        )
        try:
            yield placeholder_event
        except Exception as exc:
            status = "failed"
            meta["error"] = type(exc).__name__
            raise
        finally:
            try:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                final_event = FlowEvent(
                    event_id=event_id,
                    flow_id=resolved_flow_id,
                    correlation_id=resolved_corr_id,
                    parent_event_id=resolved_parent_id,
                    source=resolved_source,
                    destination=destination,
                    action=action,
                    data_labels=labels,
                    metadata=meta,
                    status=status,
                    duration_ms=round(duration_ms, 2),
                )
                _record_safely(final_event, client, raise_transport_errors)
            except Exception as capture_exc:
                if raise_transport_errors:
                    raise
                logger.warning("AMGR telemetry capture failed: %s", capture_exc)


def _record_safely(event: FlowEvent, client: Any | None, raise_transport_errors: bool) -> None:
    c = _resolve_client(client)
    if c is not None:
        try:
            c.record(event)
        except Exception as exc:
            if raise_transport_errors:
                raise
            logger.warning("Failed to dispatch FlowEvent %s: %s", event.event_id, exc)