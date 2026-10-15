"""Shared runtime event schemas used by the backend API."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


DISALLOWED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "api_key", "apikey", "secret", "password", "auth_token", "bearer_token",
        "private_key", "access_token", "authorization", "prompt", "raw_prompt",
        "raw_payload", "db_record", "database_record",
    }
)


class ComponentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: str = Field(min_length=1, max_length=200)
    component_type: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=200)

    @field_validator("component_id", "component_type")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Component identifiers and types must not be empty.")
        return value


class FlowEvent(BaseModel):
    """Immutable event payload. Scope is part of the contract and is authorized by the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    application_id: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)
    flow_id: UUID
    correlation_id: UUID | None = None
    parent_event_id: UUID | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: ComponentRef
    destination: ComponentRef
    action: str = Field(min_length=1, max_length=500)
    data_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="observed", min_length=1, max_length=100)
    duration_ms: float | None = Field(default=None, ge=0)

    @field_validator("application_id", "environment", "action", "status")
    @classmethod
    def normalize_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be empty or whitespace-only.")
        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("data_labels")
    @classmethod
    def validate_data_labels(cls, value: list[str]) -> list[str]:
        labels: list[str] = []
        for label in value:
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Data labels must be non-empty strings.")
            if label.strip() not in labels:
                labels.append(label.strip())
        return labels

    @field_validator("metadata")
    @classmethod
    def reject_sensitive_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    normalized = key.lower().replace("-", "_") if isinstance(key, str) else ""
                    if normalized in DISALLOWED_METADATA_KEYS and nested != "[REDACTED]":
                        raise ValueError(f"Sensitive metadata key {key!r} must be redacted before ingestion.")
                    visit(nested)
            elif isinstance(item, list):
                for nested in item:
                    visit(nested)

        visit(value)
        return value


class IngestionStatus(str, Enum):
    ACCEPTED = "accepted"
    ALREADY_PROCESSED = "already_processed"


class FlowEventIngestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    duplicate: bool
    status: IngestionStatus
    flow_id: UUID
    edge_id: UUID | None = None
    source_node_id: UUID | None = None
    destination_node_id: UUID | None = None
    message: str | None = None


# Compatibility name retained for the earlier API response contract.
FlowEventIngestResponse = FlowEventIngestionResponse
