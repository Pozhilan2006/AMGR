from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ComponentRef(BaseModel):
    component_id: str
    component_type: str
    name: str | None = None


class FlowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    flow_id: UUID
    correlation_id: UUID | None = None
    parent_event_id: UUID | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: ComponentRef
    destination: ComponentRef
    action: str
    data_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "observed"
    duration_ms: float | None = Field(default=None, ge=0)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class IngestionStatus(str, Enum):
    """Status indicating whether an incoming event was freshly ingested or recognized as a retry."""

    ACCEPTED = "accepted"
    ALREADY_PROCESSED = "already_processed"


class FlowEventIngestionResponse(BaseModel):
    """Response envelope for runtime flow event ingestion."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    status: IngestionStatus
    flow_id: UUID
    edge_id: UUID | None = None
    source_node_id: UUID | None = None
    destination_node_id: UUID | None = None
    message: str | None = None