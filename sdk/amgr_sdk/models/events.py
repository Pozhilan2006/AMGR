from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ComponentRef(BaseModel):
    component_id: str = Field(min_length=1, max_length=200)
    component_type: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=200)


class FlowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)