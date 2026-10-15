from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.ai_operator.schemas import OperatorScope
from app.schemas.flow_event import FlowEvent

LineageDirection = Literal["upstream", "downstream", "both"]
LineageCompleteness = Literal["complete", "incomplete", "unknown"]


class LineageNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: Literal["event", "component"]
    node_id: str
    label: str | None = None
    component_type: str | None = None


class LineageEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["event", "component"]
    source_id: str
    target_type: Literal["event", "component"]
    target_id: str
    relationship_type: Literal["explicit_parent_event", "observed_interaction"]
    evidence_type: Literal["recorded_parent_event_id", "recorded_event_components"]
    event_id: str


class LineageTraversal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: LineageDirection
    max_depth: int | None = None
    limit: int
    offset: int = 0
    next_offset: int | None = None


class LineageRecordView(BaseModel):
    """Stored evidence record tied to an event in the returned scope."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")

    record_id: UUID = Field(default_factory=uuid4)
    flow_id: UUID
    application_id: str
    environment: str
    originating_event_id: UUID
    parent_event_id: UUID | None = None
    source_node_id: UUID | None = None
    destination_node_id: UUID | None = None
    lineage_classification: Literal[
        "directly_observed", "explicitly_derived", "unknown_or_incomplete"
    ]
    evidence_source: str
    action: str
    data_labels: list[str] = Field(default_factory=list)
    transformation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("transformation_metadata", "metadata", "safe_metadata"),
    )
    depth: int | None = Field(default=None, ge=0)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("data_labels", mode="before")
    @classmethod
    def default_data_labels(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("transformation_metadata", mode="before")
    @classmethod
    def default_metadata(cls, value: Any) -> Any:
        return {} if value is None else value


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[FlowEvent]
    lineage_records: list[LineageRecordView] = Field(default_factory=list)
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    unresolved_parent_event_ids: list[str]
    completeness: LineageCompleteness
    truncated: bool
    truncation_reasons: list[
        Literal["cycle_detected", "depth_limit", "result_limit", "page_limit"]
    ]
    traversal: LineageTraversal
    scope: OperatorScope
