from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.flow_event import ComponentRef


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    component: ComponentRef
    label: str | None = None
    component_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    action: str
    relationship_type: Literal["observed_interaction"] = "observed_interaction"
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    truncated: bool = False
    total_nodes: int | None = None
    total_edges: int | None = None
    message: str | None = None


class GraphNeighborEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    component_type: str | None = None
    relationship: Literal["incoming", "outgoing", "both"]
    event_id: str | None = None
    flow_id: str | None = None
    action: str | None = None


class GraphNeighborhoodResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    component_type: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    neighbors: list[GraphNeighborEntry] = Field(default_factory=list)
    unavailable: bool = False
    message: str | None = None


class GraphPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_id: str
    nodes: list[str]
    edges: list[str]
    evidence: list[str] = Field(default_factory=list)


class GraphPathResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_component_id: str
    destination_component_id: str
    paths: list[GraphPath] = Field(default_factory=list)
    truncated: bool = False
    completeness: Literal["complete", "incomplete"] = "complete"
    message: str | None = None
