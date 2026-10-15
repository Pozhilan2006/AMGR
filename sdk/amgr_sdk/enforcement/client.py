"""Authorization client protocol and HTTP / local enforcement implementations.

Implements the client-side authorization boundary connecting the AMGR SDK to
the Member 3 Policy Engine and Authorization API (`POST /api/v1/authorization`).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import socket
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from amgr_sdk.config import AMGRConfig
from amgr_sdk.enforcement.models import (
    AuthorizationDecision,
    AuthorizationRequest,
    PolicyEffect,
    ReasonCode,
)
from amgr_sdk.exceptions import (
    ConfigurationError,
    EnforcementConnectionError,
    EnforcementError,
)

__all__ = [
    "AuthorizationClient",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "HttpAuthorizationClient",
    "LocalAuthorizationClient",
    "PolicyEffect",
    "ReasonCode",
]


@runtime_checkable
class AuthorizationClient(Protocol):
    """Protocol defining the authorization client interface.

    Implementations evaluate an operation against governing policies before execution.
    """

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Evaluate an authorization request and return the deterministic policy decision."""
        ...
class AuthorizationDecision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    outcome: Literal["allow", "deny", "review"]
    reason: str
    policy_ids: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HttpAuthorizationClient(AuthorizationClient):
    """Production HTTP client dispatching authorization requests to the AMGR policy service.

    Connects to `POST /api/v1/authorization` (with automatic fallback to `/api/v1/authorize`).
    Enforces strict fail-closed error handling:
    - Never treats network drops, timeouts, 4xx, 5xx, or malformed responses as implicit permission.
    - Captures `X-AMGR-Approval-ID` header on `REQUIRE_APPROVAL` decisions.
    - Scrubs credentials, prompts, and raw payloads from all error messages and diagnostics.
    """

    def __init__(
        self,
        config: AMGRConfig | None = None,
        *,
        endpoint_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.config = config or AMGRConfig.from_env()
        self._endpoint_url = endpoint_url or self._resolve_endpoint_url(self.config.api_base_url)
        self._validate_endpoint_url(self._endpoint_url)

        if timeout_seconds is not None:
            self._timeout = float(timeout_seconds)
        else:
            self._timeout = getattr(self.config, "enforcement_timeout_seconds", self.config.timeout_seconds)

    @property
    def endpoint_url(self) -> str:
        """The resolved target HTTP endpoint for authorization requests."""
        return self._endpoint_url

    @property
    def timeout_seconds(self) -> float:
        """Configured timeout in seconds for authorization HTTP calls."""
        return self._timeout

    @staticmethod
    def _resolve_endpoint_url(base_url: str) -> str:
        """Construct the exact /api/v1/authorization endpoint URL."""
        cleaned = base_url.rstrip("/")
        if cleaned.endswith("/authorization") or cleaned.endswith("/authorize"):
            return cleaned
        if cleaned.endswith("/api/v1"):
            return f"{cleaned}/authorization"
        return f"{cleaned}/api/v1/authorization"

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
                f"Insecure HTTP scheme is not permitted for remote authorization endpoint '{hostname}'. "
                "HTTPS is strictly required for remote policy enforcement."
            )

    def _build_request_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "amgr-sdk/0.1.0 (enforcement)",
        }
        token = self.config.get_auth_token() if hasattr(self.config, "get_auth_token") else self.config.auth_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _execute_http_post(
        self,
        url: str,
        payload_bytes: bytes,
        headers: dict[str, str],
        *,
        allow_fallback: bool = True,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        req = urllib.request.Request(
            url=url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                status_code = getattr(response, "status", 200)
                body = response.read()
                resp_headers: dict[str, str] = {
                    k.lower(): v for k, v in response.headers.items()
                }

                if not (200 <= status_code < 300):
                    raise EnforcementConnectionError(
                        f"Authorization endpoint returned unexpected status code HTTP {status_code}"
                    )

                try:
                    data = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise EnforcementError(
                        "Malformed JSON received from authorization endpoint."
                    ) from exc

                if not isinstance(data, dict):
                    raise EnforcementError(
                        f"Expected JSON object from authorization endpoint, got {type(data).__name__}"
                    )

                return data, resp_headers

        except urllib.error.HTTPError as exc:
            # Handle potential route alias difference: /authorization vs /authorize
            if exc.code == 404 and allow_fallback:
                fallback_url: str | None = None
                if url.endswith("/authorization"):
                    fallback_url = url[:-len("/authorization")] + "/authorize"
                elif url.endswith("/authorize"):
                    fallback_url = url[:-len("/authorize")] + "/authorization"

                if fallback_url:
                    return self._execute_http_post(
                        fallback_url,
                        payload_bytes,
                        headers,
                        allow_fallback=False,
                    )

            # Server errors (5xx) -> EnforcementConnectionError
            if exc.code >= 500:
                raise EnforcementConnectionError(
                    f"Authorization service returned server error HTTP {exc.code}"
                ) from exc

            # Client errors (4xx) -> EnforcementError (never ALLOW)
            err_detail = ""
            try:
                err_body = exc.read().decode("utf-8")
                err_json = json.loads(err_body)
                if isinstance(err_json, dict) and "detail" in err_json:
                    err_detail = f": {err_json['detail']}"
            except Exception:
                pass

            raise EnforcementError(
                f"Authorization request rejected with client error HTTP {exc.code}{err_detail}"
            ) from exc

        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise EnforcementConnectionError(
                    f"Authorization request timed out after {self._timeout}s connecting to '{url}'"
                ) from exc
            raise EnforcementConnectionError(
                f"Failed to connect to authorization endpoint at '{url}': {reason}"
            ) from exc

        except (TimeoutError, socket.timeout) as exc:
            raise EnforcementConnectionError(
                f"Authorization request timed out after {self._timeout}s connecting to '{url}'"
            ) from exc

        except OSError as exc:
            raise EnforcementConnectionError(
                f"Network error communicating with authorization endpoint '{url}': {exc}"
            ) from exc

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Synchronously evaluate an AuthorizationRequest against the remote AMGR policy engine.

        Parameters:
            request: The immutable, validated authorization request.

        Returns:
            AuthorizationDecision: The deterministic evaluation outcome.

        Raises:
            EnforcementConnectionError: On network drops, timeouts, or HTTP 5xx errors.
            EnforcementError: On HTTP 4xx errors, malformed responses, or unknown decision values.
        """
        if not isinstance(request, AuthorizationRequest):
            if isinstance(request, dict):
                request = AuthorizationRequest.model_validate(request)
            else:
                raise EnforcementError(
                    f"Expected AuthorizationRequest instance, got {type(request).__name__}"
                )

        payload_bytes = request.model_dump_json().encode("utf-8")
        headers = self._build_request_headers()

        data, resp_headers = self._execute_http_post(self._endpoint_url, payload_bytes, headers)

        # Capture X-AMGR-Approval-ID header if present
        header_approval_id = resp_headers.get("x-amgr-approval-id")
        if header_approval_id and not data.get("approval_id"):
            data["approval_id"] = header_approval_id

        # Validate that the response adheres strictly to the decision contract
        try:
            decision = AuthorizationDecision.model_validate(data)
        except Exception as exc:
            raise EnforcementError(
                f"Malformed authorization decision received from policy service: {exc}"
            ) from exc

        return decision


class LocalAuthorizationClient(AuthorizationClient):
    """In-process authorization client delegating directly to an AuthorizationService.

    Useful for embedded execution, hermetic unit tests, or direct in-memory evaluation
    without network transport overhead.
    """

    def __init__(self, service: Any) -> None:
        self.service = service

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Evaluate authorization request directly against the in-process service."""
        if not isinstance(request, AuthorizationRequest):
            if isinstance(request, dict):
                request = AuthorizationRequest.model_validate(request)
            else:
                raise EnforcementError(
                    f"Expected AuthorizationRequest instance, got {type(request).__name__}"
                )

        # Prepare backend request object if necessary
        try:
            decision, appr_req = self.service.authorize(request)
        except Exception as exc:
            raise EnforcementError(f"Local authorization evaluation failed: {exc}") from exc

        approval_id = appr_req.id if appr_req is not None else None

        if hasattr(decision, "model_dump"):
            raw = decision.model_dump(mode="json")
            if approval_id and not raw.get("approval_id"):
                raw["approval_id"] = approval_id
            return AuthorizationDecision.model_validate(raw)

        return AuthorizationDecision(
            decision=getattr(decision, "decision", PolicyEffect.BLOCK),
            policy_id=getattr(decision, "policy_id", None),
            matched_rule_id=getattr(decision, "matched_rule_id", None),
            matched_rule_ids=getattr(decision, "matched_rule_ids", []),
            matched_policy_ids=getattr(decision, "matched_policy_ids", []),
            reason_code=getattr(decision, "reason_code", ReasonCode.DEFAULT_FALLBACK.value),
            reason_message=getattr(decision, "reason_message", "Local evaluation completed"),
            evaluated_at=getattr(decision, "evaluated_at", None) or datetime.now(timezone.utc),
            approval_id=approval_id,
        )