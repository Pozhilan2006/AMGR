from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.flow_event import ComponentRef


class GraphNode(BaseModel):
    """Pydantic schema representing an observed component node in the flow graph."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    # Core node identity and categorisation
    node_id: UUID = Field(
        default_factory=uuid4,
        validation_alias=AliasChoices("node_id", "id"),
        description="Internal stable primary identifier for the graph node.",
    )
    component_type: str = Field(
        ...,
        description="Component category (e.g., agent, model, api, db, tool).",
    )
    external_component_id: str = Field(
        ...,
        description="Stable external component identifier supplied by SDK instrumentation.",
    )

    # Optional labels and scope
    display_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display_name", "label"),
        description="Optional human-readable label or display name.",
    )
    application_id: str | None = Field(
        default=None,
        description="Optional application boundary or service group identifier.",
    )
    environment: str = Field(
        default="development",
        description="Deployment environment scope (e.g., development, staging, production).",
    )

    # Safe metadata (sanitized runtime attributes only)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("node_metadata", "safe_metadata", "metadata"),
        description="Sanitized component metadata (no raw prompts, credentials, or payloads).",
    )

    # Observation timestamps (timezone-aware UTC)
    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this node was first observed.",
    )
    last_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this node was most recently observed.",
    )

    @field_validator("first_seen_at", "last_seen_at", mode="before")
    @classmethod
    def _default_timestamp_if_none(cls, value: Any) -> Any:
        if value is None:
            return datetime.now(timezone.utc)
        return value

    @field_validator("environment", mode="before")
    @classmethod
    def _default_environment_if_none(cls, value: Any) -> Any:
        if value is None:
            return "development"
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _default_metadata_if_none(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @model_validator(mode="before")
    @classmethod
    def _backward_compat_shim(cls, data: Any) -> Any:
        """Support construction from initial placeholder schema ({id, component, label})."""
        if isinstance(data, dict):
            component = data.get("component")
            if component is not None:
                if hasattr(component, "component_id"):
                    data.setdefault("external_component_id", component.component_id)
                    data.setdefault("component_type", component.component_type)
                    data.setdefault("display_name", getattr(component, "name", None))
                elif isinstance(component, dict):
                    data.setdefault("external_component_id", component.get("component_id"))
                    data.setdefault("component_type", component.get("component_type"))
                    data.setdefault("display_name", component.get("name"))
        return data

    @property
    def id(self) -> str:
        """Backward-compatible string identifier accessor."""
        return str(self.node_id)

    @property
    def label(self) -> str | None:
        """Backward-compatible label accessor."""
        return self.display_name

    @property
    def component(self) -> ComponentRef:
        """Backward-compatible ComponentRef accessor."""
        return ComponentRef(
            component_id=self.external_component_id,
            component_type=self.component_type,
            name=self.display_name,
        )


class GraphEdge(BaseModel):
    """Pydantic schema representing a logical directed interaction edge in the flow graph."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    # Core edge identity and endpoints
    edge_id: UUID = Field(
        default_factory=uuid4,
        validation_alias=AliasChoices("edge_id", "id"),
        description="Internal stable primary identifier for the logical graph edge.",
    )
    source_node_id: UUID = Field(
        ...,
        validation_alias=AliasChoices("source_node_id", "source"),
        description="Internal UUID of the origin/calling GraphNode.",
    )
    destination_node_id: UUID = Field(
        ...,
        validation_alias=AliasChoices("destination_node_id", "target"),
        description="Internal UUID of the target/callee GraphNode.",
    )

    # Action and observation metrics
    action: str = Field(
        ...,
        description="Operation or action name representing this directed interaction.",
    )
    first_observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this directed interaction was first observed.",
    )
    last_observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this directed interaction was most recently observed.",
    )
    total_events_count: int = Field(
        default=1,
        ge=1,
        description="Cumulative count of individual events observed traversing this logical edge.",
    )
    last_status: str = Field(
        default="observed",
        description="Latest observed event status (e.g., 'observed', 'blocked').",
    )

    # Classifications and safe metadata
    data_labels: list[str] = Field(
        default_factory=list,
        description="Aggregated list of distinct data sensitivity classification labels observed on this edge.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("edge_metadata", "safe_metadata", "metadata"),
        description="Sanitized edge metadata (no raw prompts, credentials, or payloads).",
    )

    @field_validator("first_observed_at", "last_observed_at", mode="before")
    @classmethod
    def _default_timestamp_if_none(cls, value: Any) -> Any:
        if value is None:
            return datetime.now(timezone.utc)
        return value

    @field_validator("last_status", mode="before")
    @classmethod
    def _default_last_status_if_none(cls, value: Any) -> Any:
        if value is None:
            return "observed"
        return value

    @field_validator("data_labels", mode="before")
    @classmethod
    def _default_data_labels_if_none(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _default_metadata_if_none(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @property
    def id(self) -> str:
        """Backward-compatible string identifier accessor."""
        return str(self.edge_id)

    @property
    def source(self) -> str:
        """Backward-compatible string source accessor."""
        return str(self.source_node_id)

    @property
    def target(self) -> str:
        """Backward-compatible string target accessor."""
        return str(self.destination_node_id)


class GraphResponse(BaseModel):
    """Pydantic response schema for flow graph queries."""

    model_config = ConfigDict(from_attributes=True)

    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="Collection of observed graph nodes.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="Collection of observed logical directed edges connecting the nodes.",
    )
    environment: str | None = Field(
        default=None,
        description="Optional environment scope filter applied to this graph view.",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when this graph response was generated.",
    )


# Architectural aliases
GraphNodeResponse = GraphNode
GraphEdgeResponse = GraphEdge