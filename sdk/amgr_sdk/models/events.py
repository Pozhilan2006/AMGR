from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Standard component category names recognized across AMGR systems.
# Preserves existing taxonomy (agent, model, api, db/database, tool, service)
# plus origin tiers (user, frontend/ui).
STANDARD_COMPONENT_TYPES: tuple[str, ...] = (
    "user",
    "frontend",
    "ui",
    "agent",
    "model",
    "tool",
    "db",
    "database",
    "api",
    "service",
    "external_service",
)

# Standard event processing statuses.
STANDARD_EVENT_STATUSES: tuple[str, ...] = (
    "observed",
    "blocked",
    "allowed",
    "pending",
    "completed",
    "failed",
)

# Keys disallowed in event metadata to prevent credential, prompt, or record leakage in telemetry.
DISALLOWED_METADATA_KEYS: frozenset[str] = frozenset({
    "api_key",
    "apikey",
    "secret",
    "password",
    "auth_token",
    "bearer_token",
    "private_key",
    "access_token",
    "authorization",
    "prompt",
    "raw_prompt",
    "raw_payload",
    "db_record",
    "database_record",
})


class ComponentRef(BaseModel):
    """Component descriptor capturing identity and category for runtime nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: str = Field(
        ...,
        min_length=1,
        description="Stable identifier for the component (e.g. agent-1, gpt-4o, sql-db).",
    )
    component_type: str = Field(
        ...,
        min_length=1,
        description="Category such as agent, model, api, db/database, tool, or service.",
    )
    name: str | None = Field(
        default=None,
        description="Optional human-readable name for the component.",
    )

    @field_validator("component_id", "component_type")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field must not be empty or whitespace-only.")
        return stripped

    @classmethod
    def user(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for user components."""
        return cls(component_id=component_id, component_type="user", name=name)

    @classmethod
    def frontend(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for frontend / client UI components."""
        return cls(component_id=component_id, component_type="frontend", name=name)

    @classmethod
    def agent(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for autonomous agent components."""
        return cls(component_id=component_id, component_type="agent", name=name)

    @classmethod
    def model(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for AI foundation model / LLM components."""
        return cls(component_id=component_id, component_type="model", name=name)

    @classmethod
    def tool(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for tool, plugin, or sandbox components."""
        return cls(component_id=component_id, component_type="tool", name=name)

    @classmethod
    def database(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for database components."""
        return cls(component_id=component_id, component_type="db", name=name)

    @classmethod
    def db(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Alias factory for database components."""
        return cls.database(component_id=component_id, name=name)

    @classmethod
    def service(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for internal or external service / API components."""
        return cls(component_id=component_id, component_type="service", name=name)

    @classmethod
    def api(cls, component_id: str, name: str | None = None) -> "ComponentRef":
        """Factory for REST/gRPC API components."""
        return cls(component_id=component_id, component_type="api", name=name)


class FlowEvent(BaseModel):
    """Immutable data-flow event representing runtime interaction between components."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this individual event.",
    )
    flow_id: UUID = Field(
        ...,
        description="Correlation key for the runtime flow or execution tree as a whole.",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Optional correlated request or session identifier.",
    )
    parent_event_id: UUID | None = Field(
        default=None,
        description="Optional reference to the initiating event in a causal chain.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when the event was generated.",
    )
    source: ComponentRef = Field(
        ...,
        description="Descriptor of the origin component initiating the interaction.",
    )
    destination: ComponentRef = Field(
        ...,
        description="Descriptor of the target component receiving the interaction.",
    )
    action: str = Field(
        ...,
        min_length=1,
        description="Operation or event label carried by the event (e.g. model.inference).",
    )
    data_labels: list[str] = Field(
        default_factory=list,
        description="Classification labels describing sensitivity or data properties (e.g. pii, internal).",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible metadata for observability. Secrets and raw credentials are forbidden.",
    )
    status: str = Field(
        default="observed",
        min_length=1,
        description="Business or processing status, such as 'observed' or 'blocked'.",
    )
    duration_ms: float | None = Field(
        default=None,
        ge=0,
        description="Optional measured duration of the operation in milliseconds (non-negative).",
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert the FlowEvent to a JSON-serializable dictionary."""
        return self.model_dump(mode="json")

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the FlowEvent to an ISO/UTC JSON string."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowEvent":
        """Deserialize and validate a FlowEvent from a dictionary payload."""
        return cls.model_validate(data)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("action", "status")
    @classmethod
    def validate_non_empty_str(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field must not be empty or whitespace-only.")
        return stripped

    @field_validator("data_labels")
    @classmethod
    def validate_data_labels(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for label in value:
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Data labels must be non-empty strings.")
            stripped = label.strip()
            if stripped not in cleaned:
                cleaned.append(stripped)
        return cleaned

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if isinstance(key, str) and key.lower().replace("-", "_") in DISALLOWED_METADATA_KEYS:
                # If value is already redacted, permit the key
                if value[key] == "[REDACTED]":
                    continue
                raise ValueError(
                    f"Sensitive key {key!r} is not permitted in event metadata. "
                    "Secrets and credentials must not be emitted in telemetry."
                )
        return value


def sanitize_metadata(
    metadata: dict[str, Any],
    *,
    additional_sensitive_keys: tuple[str, ...] | Sequence[str] = (),
    redact: bool = True,
    redaction_placeholder: str = "[REDACTED]",
) -> dict[str, Any]:
    """Sanitize metadata dictionary by masking or validating sensitive keys.

    Recursively sanitizes nested dictionaries, ensuring application-owned
    dictionaries are never mutated in place.

    If redact is True, values corresponding to disallowed or sensitive keys are
    replaced with redaction_placeholder. If False, a ValueError is raised upon
    detecting a sensitive key.
    """
    sensitive_keys = set(DISALLOWED_METADATA_KEYS)
    for k in additional_sensitive_keys:
        if isinstance(k, str) and k.strip():
            sensitive_keys.add(k.strip().lower().replace("-", "_"))

    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(key, str) and key.lower().replace("-", "_") in sensitive_keys:
            if redact:
                sanitized[key] = redaction_placeholder
            else:
                raise ValueError(
                    f"Sensitive key {key!r} is not permitted in event metadata. "
                    "Secrets and credentials must not be emitted in telemetry."
                )
        elif isinstance(value, dict):
            sanitized[key] = sanitize_metadata(
                value,
                additional_sensitive_keys=additional_sensitive_keys,
                redact=redact,
                redaction_placeholder=redaction_placeholder,
            )
        else:
            sanitized[key] = value
    return sanitized