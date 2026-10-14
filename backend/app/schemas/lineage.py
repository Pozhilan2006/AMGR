"""Pydantic schemas for runtime flow lineage and provenance contracts."""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# Valid classifications matching database check constraint
LineageClassification = Literal[
    "directly_observed",
    "explicitly_derived",
    "unknown_or_incomplete",
]


class LineageRecord(BaseModel):
    """Pydantic schema representing a single causal lineage transition and provenance evidence."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    # Core lineage identity
    record_id: UUID = Field(
        default_factory=uuid4,
        description="Internal primary identifier for the lineage record.",
    )
    flow_id: UUID = Field(
        ...,
        description="Runtime flow session identifier.",
    )

    # Event references
    originating_event_id: UUID = Field(
        ...,
        description="UUID of the FlowEvent that produced or evidenced this transition.",
    )
    parent_event_id: UUID | None = Field(
        default=None,
        description="Optional UUID of the causal parent FlowEvent.",
    )

    # Optional resolved node references
    source_node_id: UUID | None = Field(
        default=None,
        description="Optional internal UUID of the origin GraphNode.",
    )
    destination_node_id: UUID | None = Field(
        default=None,
        description="Optional internal UUID of the destination GraphNode.",
    )

    # Classification and grounding evidence
    lineage_classification: LineageClassification = Field(
        ...,
        description="Reliability tier ('directly_observed', 'explicitly_derived', 'unknown_or_incomplete').",
    )
    evidence_source: str = Field(
        ...,
        description="Source of lineage evidence (e.g., 'sdk_runtime_event', 'context_propagation').",
    )

    # Transformation details
    action: str = Field(
        ...,
        description="Action or transformation performed during this transition.",
    )
    data_labels: list[str] = Field(
        default_factory=list,
        description="Sensitivity classification labels associated with this lineage step.",
    )
    transformation_metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("transformation_metadata", "metadata", "safe_metadata"),
        description="Sanitized transformation metadata (strictly no raw prompts, credentials, or payloads).",
    )

    # Topological distance
    depth: int | None = Field(
        default=None,
        ge=0,
        description="Optional distance/depth from root event in the causal flow graph (nonnegative).",
    )

    # Occurrence timestamp
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this lineage record was recorded.",
    )

    @field_validator("recorded_at", mode="before")
    @classmethod
    def _default_timestamp_if_none(cls, value: Any) -> Any:
        if value is None:
            return datetime.now(timezone.utc)
        return value

    @field_validator("data_labels", mode="before")
    @classmethod
    def _default_data_labels_if_none(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_validator("transformation_metadata", mode="before")
    @classmethod
    def _default_transformation_metadata_if_none(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value


class FlowLineageResponse(BaseModel):
    """Pydantic response schema for flow lineage queries."""

    model_config = ConfigDict(from_attributes=True)

    flow_id: UUID = Field(
        ...,
        description="Runtime flow session identifier.",
    )
    records: list[LineageRecord] = Field(
        default_factory=list,
        description="Collection of chronological or hierarchical lineage records for this flow.",
    )
    total_records: int = Field(
        default=0,
        ge=0,
        description="Total count of lineage records in this response.",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this response was generated.",
    )


# Architectural aliases
LineageRecordResponse = LineageRecord
LineageResponse = FlowLineageResponse
