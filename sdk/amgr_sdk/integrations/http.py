from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
import functools
import inspect
import logging
from typing import Any, Iterator, TypeVar
import urllib.error
from urllib.parse import urlsplit, urlunsplit
import urllib.request
from uuid import uuid4

from amgr_sdk.capture.recorder import ActiveOperation, EventRecorder, _resolve_data_labels
from amgr_sdk.exceptions import IntegrationError
from amgr_sdk.models.events import ComponentRef, sanitize_metadata

logger = logging.getLogger("amgr_sdk.integrations.http")

F = TypeVar("F", bound=Callable[..., Any])


def normalize_url(url: str) -> str:
    """Normalize a target URL by removing query parameters, fragments, and credentials.

    Prevents leaking query parameters (which frequently contain tokens or PII),
    URL fragments, or embedded userinfo credentials into telemetry events.

    Example:
        'https://user:pass@api.stripe.com/v1/charges?key=secret#top'
        -> 'https://api.stripe.com/v1/charges'
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        clean_netloc = f"{hostname}{port}" if hostname else parts.netloc
        return urlunsplit((parts.scheme, clean_netloc, parts.path, "", ""))
    except Exception:
        # Fallback if URL parsing fails
        return url.split("?")[0].split("#")[0]


def derive_service_ref(url: str, default_service: ComponentRef | None = None) -> ComponentRef:
    """Derive a ComponentRef representing the destination HTTP service from a URL."""
    if default_service is not None:
        return default_service
    try:
        parts = urlsplit(url)
        host = parts.hostname or "unknown-http-service"
        return ComponentRef(
            component_id=host,
            component_type="service",
            name=host,
        )
    except Exception:
        return ComponentRef(
            component_id="unknown-http-service",
            component_type="service",
            name="HTTP Service",
        )


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
        logger.debug("Could not resolve default EventRecorder for HTTP integration: %s", exc)
        return None


@contextmanager
def http_span(
    url: str,
    method: str = "GET",
    *,
    destination: ComponentRef | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    status: str = "completed",
) -> Iterator[ActiveOperation]:
    """Context manager for instrumenting an outbound HTTP request block.

    Captures method, normalized URL (without query params or fragments),
    duration, and status code while ensuring bodies and headers are excluded.
    """
    clean_url = normalize_url(url)
    clean_method = method.upper().strip()
    dest = derive_service_ref(clean_url, destination)
    labels = _resolve_data_labels(data_labels)

    rec = _resolve_recorder(client, recorder)
    safe_meta = {
        "method": clean_method,
        "url": clean_url,
    }
    if metadata:
        # Merge caller metadata through centralized sanitization
        safe_meta.update(sanitize_metadata(metadata, redact=True))

    if rec is None:
        from amgr_sdk.transport.base import Transport

        class _NoOpTransport(Transport):
            def send(self, event: Any) -> None:
                pass

            def send_batch(self, events: Any) -> None:
                pass

        dummy_op = ActiveOperation(
            recorder=EventRecorder(_NoOpTransport(), recording_enabled=False),
            action="http.request",
            destination=dest,
            source=source,
            data_labels=labels,
            metadata=safe_meta,
            flow_id=uuid4(),
        )
        yield dummy_op
        return

    with rec.start_operation(
        action="http.request",
        destination=dest,
        source=source,
        data_labels=labels,
        metadata=safe_meta,
        enter_context=True,
    ) as op:
        yield op


def capture_http(
    url: str | None = None,
    method: str = "GET",
    *,
    destination: ComponentRef | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    client: Any | None = None,
    recorder: EventRecorder | None = None,
    status: str = "completed",
) -> Callable[[F], F]:
    """Decorator to instrument a function performing an outbound HTTP request."""

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                target_url = url or (args[0] if args and isinstance(args[0], str) else "")
                with http_span(
                    url=target_url,
                    method=method,
                    destination=destination,
                    source=source,
                    data_labels=data_labels,
                    metadata=metadata,
                    client=client,
                    recorder=recorder,
                    status=status,
                ) as op:
                    result = await func(*args, **kwargs)
                    if hasattr(result, "status_code"):
                        op.add_metadata("status_code", getattr(result, "status_code"))
                    elif hasattr(result, "status"):
                        op.add_metadata("status_code", getattr(result, "status"))
                    elif hasattr(result, "code"):
                        op.add_metadata("status_code", getattr(result, "code"))
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            target_url = url or (args[0] if args and isinstance(args[0], str) else "")
            with http_span(
                url=target_url,
                method=method,
                destination=destination,
                source=source,
                data_labels=data_labels,
                metadata=metadata,
                client=client,
                recorder=recorder,
                status=status,
            ) as op:
                result = func(*args, **kwargs)
                if hasattr(result, "status_code"):
                    op.add_metadata("status_code", getattr(result, "status_code"))
                elif hasattr(result, "status"):
                    op.add_metadata("status_code", getattr(result, "status"))
                elif hasattr(result, "code"):
                    op.add_metadata("status_code", getattr(result, "code"))
                return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def instrumented_urlopen(
    url_or_request: str | urllib.request.Request,
    data: Any = None,
    timeout: float | None = None,
    *,
    recorder: EventRecorder | None = None,
    client: Any | None = None,
    destination: ComponentRef | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Instrumented drop-in replacement for urllib.request.urlopen.

    Records outbound HTTP request events with duration, normalized URL,
    HTTP method, and response status code through the central EventRecorder.
    Original responses and exceptions (e.g. urllib.error.HTTPError) are preserved.
    """
    if isinstance(url_or_request, urllib.request.Request):
        raw_url = url_or_request.full_url
        method = url_or_request.get_method()
    else:
        raw_url = str(url_or_request)
        method = "POST" if data is not None else "GET"

    clean_url = normalize_url(raw_url)
    dest = derive_service_ref(clean_url, destination)
    labels = _resolve_data_labels(data_labels)
    rec = _resolve_recorder(client, recorder)

    safe_meta = {
        "method": method.upper(),
        "url": clean_url,
    }
    if metadata:
        safe_meta.update(sanitize_metadata(metadata, redact=True))

    if rec is None:
        if timeout is not None:
            return urllib.request.urlopen(url_or_request, data=data, timeout=timeout, **kwargs)
        return urllib.request.urlopen(url_or_request, data=data, **kwargs)

    with rec.start_operation(
        action="http.request",
        destination=dest,
        source=source,
        data_labels=labels,
        metadata=safe_meta,
        enter_context=True,
    ) as op:
        try:
            if timeout is not None:
                response = urllib.request.urlopen(url_or_request, data=data, timeout=timeout, **kwargs)
            else:
                response = urllib.request.urlopen(url_or_request, data=data, **kwargs)

            status_code = getattr(response, "status", getattr(response, "code", 200))
            op.add_metadata("status_code", status_code)
            return response
        except urllib.error.HTTPError as http_err:
            op.add_metadata("status_code", http_err.code)
            raise
        except Exception:
            raise


def instrument_requests_session(
    session: Any,
    *,
    recorder: EventRecorder | None = None,
    client: Any | None = None,
    destination: ComponentRef | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
) -> Any:
    """Opt-in instrumentation for a requests.Session instance.

    Wraps the specific session instance to record HTTP request telemetry without
    monkey-patching global requests functions or unrelated libraries.

    Raises:
        IntegrationError: If the 'requests' package is not installed.
    """
    try:
        import requests
    except ImportError as err:
        raise IntegrationError(
            "The 'requests' package is not installed in the current environment. "
            "Install it via 'pip install requests' to use Requests HTTP instrumentation."
        ) from err

    if not isinstance(session, requests.Session):
        raise TypeError(f"Expected a requests.Session instance, got {type(session).__name__}")

    rec = _resolve_recorder(client, recorder)
    original_send = session.send

    def instrumented_send(request: Any, **kwargs: Any) -> Any:
        clean_url = normalize_url(getattr(request, "url", ""))
        method = getattr(request, "method", "GET").upper()
        dest = derive_service_ref(clean_url, destination)
        labels = _resolve_data_labels(data_labels)

        if rec is None:
            return original_send(request, **kwargs)

        with rec.start_operation(
            action="http.request",
            destination=dest,
            source=source,
            data_labels=labels,
            metadata={"method": method, "url": clean_url},
            enter_context=True,
        ) as op:
            try:
                response = original_send(request, **kwargs)
                op.add_metadata("status_code", getattr(response, "status_code", 200))
                return response
            except Exception as exc:
                if hasattr(exc, "response") and exc.response is not None:
                    op.add_metadata("status_code", getattr(exc.response, "status_code", None))
                raise

    session.send = instrumented_send
    return session


def instrument_httpx_client(
    client_instance: Any,
    *,
    recorder: EventRecorder | None = None,
    client: Any | None = None,
    destination: ComponentRef | None = None,
    source: ComponentRef | None = None,
    data_labels: Sequence[str] | str | None = None,
) -> Any:
    """Opt-in instrumentation for an httpx.Client or httpx.AsyncClient instance.

    Raises:
        IntegrationError: If the 'httpx' package is not installed.
    """
    try:
        import httpx
    except ImportError as err:
        raise IntegrationError(
            "The 'httpx' package is not installed in the current environment. "
            "Install it via 'pip install httpx' to use HTTPX instrumentation."
        ) from err

    rec = _resolve_recorder(client, recorder)

    if isinstance(client_instance, httpx.Client):
        original_send = client_instance.send

        def instrumented_sync_send(request: Any, **kwargs: Any) -> Any:
            clean_url = normalize_url(str(request.url))
            method = request.method.upper()
            dest = derive_service_ref(clean_url, destination)
            labels = _resolve_data_labels(data_labels)

            if rec is None:
                return original_send(request, **kwargs)

            with rec.start_operation(
                action="http.request",
                destination=dest,
                source=source,
                data_labels=labels,
                metadata={"method": method, "url": clean_url},
                enter_context=True,
            ) as op:
                try:
                    response = original_send(request, **kwargs)
                    op.add_metadata("status_code", response.status_code)
                    return response
                except Exception as exc:
                    if hasattr(exc, "response") and exc.response is not None:
                        op.add_metadata("status_code", exc.response.status_code)
                    raise

        client_instance.send = instrumented_sync_send
        return client_instance

    elif isinstance(client_instance, httpx.AsyncClient):
        original_async_send = client_instance.send

        async def instrumented_async_send(request: Any, **kwargs: Any) -> Any:
            clean_url = normalize_url(str(request.url))
            method = request.method.upper()
            dest = derive_service_ref(clean_url, destination)
            labels = _resolve_data_labels(data_labels)

            if rec is None:
                return await original_async_send(request, **kwargs)

            with rec.start_operation(
                action="http.request",
                destination=dest,
                source=source,
                data_labels=labels,
                metadata={"method": method, "url": clean_url},
                enter_context=True,
            ) as op:
                try:
                    response = await original_async_send(request, **kwargs)
                    op.add_metadata("status_code", response.status_code)
                    return response
                except Exception as exc:
                    if hasattr(exc, "response") and exc.response is not None:
                        op.add_metadata("status_code", exc.response.status_code)
                    raise

        client_instance.send = instrumented_async_send
        return client_instance

    else:
        raise TypeError(
            f"Expected an httpx.Client or httpx.AsyncClient instance, got {type(client_instance).__name__}"
        )
