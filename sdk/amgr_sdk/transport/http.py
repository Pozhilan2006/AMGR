from collections.abc import Sequence
import asyncio
import random
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Any

from amgr_sdk.config import AMGRConfig
from amgr_sdk.exceptions import ConfigurationError
from amgr_sdk.models.events import FlowEvent
from amgr_sdk.transport.base import (
    AsyncTransport,
    BatchTransportError,
    Transport,
    TransportConnectionError,
    TransportError,
)

# HTTP status codes that indicate transient infrastructure issues eligible for retry
TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})


class HttpTransport(Transport, AsyncTransport):
    """HTTP client dispatching FlowEvent payloads to the AMGR ingestion endpoint.

    Supports both synchronous ('send', 'send_batch') and asynchronous ('send_async',
    'send_batch_async') dispatch without external third-party HTTP dependencies.
    """

    def __init__(self, config: AMGRConfig | None = None) -> None:
        self.config = config or AMGRConfig.from_env()
        self._endpoint_url = self._resolve_endpoint_url(self.config.api_base_url)
        self._validate_endpoint_url(self._endpoint_url)

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    @staticmethod
    def _resolve_endpoint_url(base_url: str) -> str:
        """Construct the exact /api/v1/flow-events URL without duplicated or missing segments."""
        cleaned = base_url.rstrip("/")
        if cleaned.endswith("/flow-events"):
            return cleaned
        if cleaned.endswith("/api/v1"):
            return f"{cleaned}/flow-events"
        return f"{cleaned}/api/v1/flow-events"

    @staticmethod
    def _validate_endpoint_url(url: str) -> None:
        """Ensure remote endpoints enforce HTTPS, permitting HTTP only for local development."""
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()

        is_local = (
            hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "testserver")
            or hostname.endswith(".local")
        )
        if scheme == "http" and not is_local:
            raise ConfigurationError(
                f"Insecure HTTP scheme is not permitted for remote endpoint '{hostname}'. "
                "HTTPS is strictly required for remote event delivery."
            )

    def _build_request(self, payload_bytes: bytes) -> urllib.request.Request:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "amgr-sdk/0.1.0",
        }
        auth_token = self.config.get_auth_token() if hasattr(self.config, "get_auth_token") else self.config.auth_token
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        return urllib.request.Request(
            url=self._endpoint_url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

    def send(self, event: FlowEvent) -> None:
        """Send a single FlowEvent to the backend with transient-error retries.

        Credentials, tokens, prompts, and sensitive metadata are strictly scrubbed
        from all error messages and diagnostics.
        """
        payload_bytes = event.model_dump_json().encode("utf-8")
        max_attempts = max(1, self.config.max_retries + 1)
        last_error: TransportError | None = None

        for attempt in range(max_attempts):
            try:
                request = self._build_request(payload_bytes)
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    if 200 <= status_code < 300:
                        return
                    raise TransportError(
                        message=f"Unexpected response status {status_code} from /api/v1/flow-events for event {event.event_id}",
                        status_code=status_code,
                        retryable=status_code in TRANSIENT_STATUS_CODES,
                    )
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code == 501:
                    # Backend endpoint is not yet implemented (HTTP 501). Explicitly non-retryable.
                    raise TransportError(
                        message=f"Backend event ingestion is not implemented (HTTP 501) at /api/v1/flow-events for event {event.event_id}.",
                        status_code=501,
                        retryable=False,
                    ) from exc

                is_retryable = code in TRANSIENT_STATUS_CODES
                last_error = TransportError(
                    message=f"HTTP request failed with status {code} at /api/v1/flow-events for event {event.event_id}.",
                    status_code=code,
                    retryable=is_retryable,
                )

                if not is_retryable or attempt == max_attempts - 1:
                    raise last_error from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # Transient network, socket, or timeout failure
                last_error = TransportConnectionError(
                    message=f"Connection failure communicating with /api/v1/flow-events for event {event.event_id}: {exc.__class__.__name__}."
                )
                if attempt == max_attempts - 1:
                    raise last_error from exc

            # Backoff before next attempt
            backoff_delay = self.config.retry_backoff_seconds * (2 ** attempt)
            if getattr(self.config, "backoff_jitter", False):
                backoff_delay = random.uniform(0.5 * backoff_delay, 1.5 * backoff_delay)
            time.sleep(backoff_delay)

        if last_error is not None:
            raise last_error

    def send_batch(self, events: Sequence[FlowEvent]) -> None:
        """Dispatch a sequence of FlowEvent objects sequentially to POST /api/v1/flow-events.

        Provides structured partial-failure reporting through BatchTransportError
        to prevent silent event loss and avoid duplicate resends.
        """
        successful: list[FlowEvent] = []

        for index, event in enumerate(events):
            try:
                self.send(event)
                successful.append(event)
            except TransportError as err:
                remaining = list(events[index + 1:])
                raise BatchTransportError(
                    message=(
                        f"Batch dispatch stopped at event {index + 1}/{len(events)} "
                        f"(event_id={event.event_id}): {err.message}"
                    ),
                    successful_events=successful,
                    failed_event=event,
                    remaining_events=remaining,
                    cause=err,
                ) from err
    async def send_async(self, event: FlowEvent) -> None:
        """Asynchronously send a single FlowEvent to the backend."""
        return await asyncio.to_thread(self.send, event)

    async def send_batch_async(self, events: Sequence[FlowEvent]) -> None:
        """Asynchronously dispatch a sequence of FlowEvents to the backend."""
        return await asyncio.to_thread(self.send_batch, events)

    # Standard async aliases
    asend = send_async
    asend_batch = send_batch_async

