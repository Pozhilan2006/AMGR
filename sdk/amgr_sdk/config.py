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
        application_id/environment: Required together when using capture APIs; these
            values are copied into each event and checked by backend identity middleware.
        capture_enabled: Master switch to enable or disable telemetry capture.
        sensitive_field_keys: Additional metadata keys to redact or reject.
        redact_sensitive_fields: If True, sensitive metadata is masked rather than rejected.
        queue_capacity: Maximum capacity of the bounded in-memory delivery queue (>= 0).
            If 0, queueing is disabled and events are dispatched directly/synchronously.
        batch_size: Maximum number of events per delivery batch (>= 1).
        flush_interval_seconds: Maximum interval in seconds between periodic queue flushes (> 0).
        queue_overflow_strategy: Policy when queue is full: 'drop_oldest', 'drop_newest', 'block', or 'raise'.
        backoff_jitter: If True, applies random jitter to exponential backoff delays.
    """

    api_base_url: str = "http://localhost:8000/api/v1"
    timeout_seconds: float = 5.0
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    auth_token: str | None = None
    token_provider: Callable[[], str | None] | None = None
    application_id: str | None = None
    environment: str | None = None
    capture_enabled: bool = True
    sensitive_field_keys: tuple[str, ...] = ()
    redact_sensitive_fields: bool = True
    queue_capacity: int = 1000
    batch_size: int = 50
    flush_interval_seconds: float = 1.0
    queue_overflow_strategy: str = "drop_oldest"
    backoff_jitter: bool = True
    shutdown_timeout_seconds: float = 5.0
    buffered: bool = False
    fail_closed: bool = True
    enforcement_timeout_seconds: float = 5.0

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

        if (self.application_id is None) != (self.environment is None):
            raise ConfigurationError("application_id and environment must be configured together.")
        if self.application_id is not None:
            if not self.application_id.strip() or len(self.application_id.strip()) > 200:
                raise ConfigurationError("application_id must contain 1 to 200 non-whitespace characters.")
            if self.environment is None or not self.environment.strip() or len(self.environment.strip()) > 100:
                raise ConfigurationError("environment must contain 1 to 100 non-whitespace characters.")

        if not isinstance(self.capture_enabled, bool):
            raise ConfigurationError(
                f"capture_enabled must be a boolean, got {type(self.capture_enabled).__name__}"
            )

        if not isinstance(self.queue_capacity, int) or self.queue_capacity < 0:
            raise ConfigurationError(
                f"queue_capacity must be a non-negative integer (>= 0), got {self.queue_capacity!r}"
            )

        if not isinstance(self.batch_size, int) or self.batch_size < 1:
            raise ConfigurationError(
                f"batch_size must be a positive integer (>= 1), got {self.batch_size!r}"
            )

        if not isinstance(self.flush_interval_seconds, (int, float)) or self.flush_interval_seconds <= 0:
            raise ConfigurationError(
                f"flush_interval_seconds must be a positive number (> 0), got {self.flush_interval_seconds!r}"
            )

        valid_overflow_strategies = ("drop_oldest", "drop_newest", "block", "raise")
        if (
            not isinstance(self.queue_overflow_strategy, str)
            or self.queue_overflow_strategy.strip().lower() not in valid_overflow_strategies
        ):
            raise ConfigurationError(
                f"queue_overflow_strategy must be one of {valid_overflow_strategies!r}, "
                f"got {self.queue_overflow_strategy!r}"
            )

        if not isinstance(self.backoff_jitter, bool):
            raise ConfigurationError(
                f"backoff_jitter must be a boolean, got {type(self.backoff_jitter).__name__}"
            )

        if not isinstance(self.shutdown_timeout_seconds, (int, float)) or self.shutdown_timeout_seconds <= 0:
            raise ConfigurationError(
                f"shutdown_timeout_seconds must be a positive number (> 0), got {self.shutdown_timeout_seconds!r}"
            )

        if not isinstance(self.buffered, bool):
            raise ConfigurationError(
                f"buffered must be a boolean, got {type(self.buffered).__name__}"
            )

        if not isinstance(self.fail_closed, bool):
            raise ConfigurationError(
                f"fail_closed must be a boolean, got {type(self.fail_closed).__name__}"
            )

        if (
            not isinstance(self.enforcement_timeout_seconds, (int, float))
            or self.enforcement_timeout_seconds <= 0
        ):
            raise ConfigurationError(
                f"enforcement_timeout_seconds must be a positive number (> 0), got {self.enforcement_timeout_seconds!r}"
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

        # AMGR_QUEUE_CAPACITY
        queue_cap_raw = os.getenv("AMGR_QUEUE_CAPACITY")
        if queue_cap_raw is not None:
            try:
                queue_capacity = int(queue_cap_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-integer value for AMGR_QUEUE_CAPACITY: {queue_cap_raw!r}"
                ) from exc
        else:
            queue_capacity = cls.queue_capacity

        # AMGR_BATCH_SIZE
        batch_size_raw = os.getenv("AMGR_BATCH_SIZE")
        if batch_size_raw is not None:
            try:
                batch_size = int(batch_size_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-integer value for AMGR_BATCH_SIZE: {batch_size_raw!r}"
                ) from exc
        else:
            batch_size = cls.batch_size

        # AMGR_FLUSH_INTERVAL_SECONDS
        flush_int_raw = os.getenv("AMGR_FLUSH_INTERVAL_SECONDS")
        if flush_int_raw is not None:
            try:
                flush_interval = float(flush_int_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-numeric value for AMGR_FLUSH_INTERVAL_SECONDS: {flush_int_raw!r}"
                ) from exc
        else:
            flush_interval = cls.flush_interval_seconds

        # AMGR_QUEUE_OVERFLOW_STRATEGY
        overflow_strategy = os.getenv("AMGR_QUEUE_OVERFLOW_STRATEGY", cls.queue_overflow_strategy).strip().lower()

        # AMGR_BACKOFF_JITTER
        jitter_raw = os.getenv("AMGR_BACKOFF_JITTER", "true").strip().lower()
        backoff_jitter = jitter_raw not in ("0", "false", "no", "off")

        # AMGR_SHUTDOWN_TIMEOUT_SECONDS
        shutdown_raw = os.getenv("AMGR_SHUTDOWN_TIMEOUT_SECONDS")
        if shutdown_raw is not None:
            try:
                shutdown_timeout = float(shutdown_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-numeric value for AMGR_SHUTDOWN_TIMEOUT_SECONDS: {shutdown_raw!r}"
                ) from exc
        else:
            shutdown_timeout = cls.shutdown_timeout_seconds

        # AMGR_BUFFERED_DELIVERY
        buffered_raw = os.getenv("AMGR_BUFFERED_DELIVERY", os.getenv("AMGR_BUFFERED", "false")).strip().lower()
        buffered = buffered_raw in ("1", "true", "yes", "on")

        # AMGR_ENFORCEMENT_FAIL_CLOSED
        fail_closed_raw = os.getenv("AMGR_ENFORCEMENT_FAIL_CLOSED", "true").strip().lower()
        fail_closed = fail_closed_raw not in ("0", "false", "no", "off")

        # AMGR_ENFORCEMENT_TIMEOUT_SECONDS
        enf_timeout_raw = os.getenv("AMGR_ENFORCEMENT_TIMEOUT_SECONDS")
        if enf_timeout_raw is not None:
            try:
                enf_timeout = float(enf_timeout_raw)
            except ValueError as exc:
                raise ConfigurationError(
                    f"Invalid non-numeric value for AMGR_ENFORCEMENT_TIMEOUT_SECONDS: {enf_timeout_raw!r}"
                ) from exc
        else:
            enf_timeout = cls.enforcement_timeout_seconds

        return cls(
            api_base_url=os.getenv("AMGR_API_BASE_URL", cls.api_base_url),
            timeout_seconds=timeout,
            max_retries=retries,
            retry_backoff_seconds=backoff,
            auth_token=os.getenv("AMGR_AUTH_TOKEN", cls.auth_token),
            application_id=os.getenv("AMGR_APPLICATION_ID"),
            environment=os.getenv("AMGR_ENVIRONMENT"),
            capture_enabled=capture_enabled,
            sensitive_field_keys=sensitive_keys,
            redact_sensitive_fields=redact,
            queue_capacity=queue_capacity,
            batch_size=batch_size,
            flush_interval_seconds=flush_interval,
            queue_overflow_strategy=overflow_strategy,
            backoff_jitter=backoff_jitter,
            shutdown_timeout_seconds=shutdown_timeout,
            buffered=buffered,
        )
            fail_closed=fail_closed,
            enforcement_timeout_seconds=enf_timeout,
        )
