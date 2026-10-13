from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os

from amgr_sdk.exceptions import ConfigurationError


@dataclass(frozen=True)
class AMGRConfig:
    """Configuration options for the AMGR SDK client, transport, and capture.

    Attributes:
        api_base_url: Base endpoint URL of the AMGR backend service.
        timeout_seconds: Request timeout in seconds for HTTP transport calls (must be > 0).
        max_retries: Maximum retry attempts for transient network or server errors (>= 0).
        retry_backoff_seconds: Initial backoff delay between retries in seconds (>= 0).
        auth_token: Optional static bearer token for backend authorization.
        token_provider: Optional callable returning a bearer token dynamically.
        capture_enabled: Master switch to enable or disable telemetry capture.
        sensitive_field_keys: Additional metadata keys to redact or reject.
        redact_sensitive_fields: If True, sensitive metadata is masked rather than rejected.
    """

    api_base_url: str = "http://localhost:8000/api/v1"
    timeout_seconds: float = 5.0
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    auth_token: str | None = None
    token_provider: Callable[[], str | None] | None = None
    capture_enabled: bool = True
    sensitive_field_keys: tuple[str, ...] = ()
    redact_sensitive_fields: bool = True

    def __post_init__(self) -> None:
        """Validate configuration settings and raise ConfigurationError if invalid."""
        if not isinstance(self.api_base_url, str) or not self.api_base_url.strip():
            raise ConfigurationError("api_base_url must be a non-empty string.")

        clean_url = self.api_base_url.strip().lower()
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            raise ConfigurationError(
                f"api_base_url must start with 'http://' or 'https://', got {self.api_base_url!r}"
            )

        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ConfigurationError(
                f"timeout_seconds must be a positive number (> 0), got {self.timeout_seconds!r}"
            )

        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ConfigurationError(
                f"max_retries must be a non-negative integer (>= 0), got {self.max_retries!r}"
            )

        if not isinstance(self.retry_backoff_seconds, (int, float)) or self.retry_backoff_seconds < 0:
            raise ConfigurationError(
                f"retry_backoff_seconds must be a non-negative number (>= 0), got {self.retry_backoff_seconds!r}"
            )

        if self.auth_token is not None and not isinstance(self.auth_token, str):
            raise ConfigurationError(
                f"auth_token must be a string if provided, got {type(self.auth_token).__name__}"
            )

        if self.token_provider is not None and not callable(self.token_provider):
            raise ConfigurationError("token_provider must be a callable if provided.")

        if not isinstance(self.capture_enabled, bool):
            raise ConfigurationError(
                f"capture_enabled must be a boolean, got {type(self.capture_enabled).__name__}"
            )

    def get_auth_token(self) -> str | None:
        """Retrieve the authentication token via the token provider or static token."""
        if self.token_provider is not None:
            return self.token_provider()
        return self.auth_token

    @classmethod
    def from_env(cls) -> "AMGRConfig":
        """Load SDK configuration from environment variables with safe defaults and validation."""
        # AMGR_CAPTURE_ENABLED
        capture_str = os.getenv("AMGR_CAPTURE_ENABLED", "true").strip().lower()
        capture_enabled = capture_str not in ("0", "false", "no", "off")

        # AMGR_TIMEOUT_SECONDS
        timeout_raw = os.getenv("AMGR_TIMEOUT_SECONDS")
        if timeout_raw is not None:
            try:
                timeout = float(timeout_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-numeric value for AMGR_TIMEOUT_SECONDS: {timeout_raw!r}"
                ) from exc
        else:
            timeout = cls.timeout_seconds

        # AMGR_MAX_RETRIES
        retries_raw = os.getenv("AMGR_MAX_RETRIES")
        if retries_raw is not None:
            try:
                retries = int(retries_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-integer value for AMGR_MAX_RETRIES: {retries_raw!r}"
                ) from exc
        else:
            retries = cls.max_retries

        # AMGR_RETRY_BACKOFF_SECONDS
        backoff_raw = os.getenv("AMGR_RETRY_BACKOFF_SECONDS")
        if backoff_raw is not None:
            try:
                backoff = float(backoff_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-numeric value for AMGR_RETRY_BACKOFF_SECONDS: {backoff_raw!r}"
                ) from exc
        else:
            backoff = cls.retry_backoff_seconds

        # AMGR_SENSITIVE_KEYS
        sensitive_raw = os.getenv("AMGR_SENSITIVE_KEYS")
        if sensitive_raw:
            sensitive_keys = tuple(k.strip() for k in sensitive_raw.split(",") if k.strip())
        else:
            sensitive_keys = cls.sensitive_field_keys

        # AMGR_REDACT_SENSITIVE_FIELDS
        redact_raw = os.getenv("AMGR_REDACT_SENSITIVE_FIELDS", "true").strip().lower()
        redact = redact_raw not in ("0", "false", "no", "off")

        return cls(
            api_base_url=os.getenv("AMGR_API_BASE_URL", cls.api_base_url),
            timeout_seconds=timeout,
            max_retries=retries,
            retry_backoff_seconds=backoff,
            auth_token=os.getenv("AMGR_AUTH_TOKEN", cls.auth_token),
            capture_enabled=capture_enabled,
            sensitive_field_keys=sensitive_keys,
            redact_sensitive_fields=redact,
        )