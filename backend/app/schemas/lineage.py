from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ai_operator.schemas import OperatorScope
from app.schemas.flow_event import FlowEvent

LineageDirection = Literal["upstream", "downstream", "both"]
LineageCompleteness = Literal["unknown", "incomplete"]


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


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[FlowEvent]
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    unresolved_parent_event_ids: list[str]
    completeness: LineageCompleteness
    truncated: bool
    truncation_reasons: list[Literal["depth_limit", "result_limit", "page_limit"]]
    traversal: LineageTraversal
    scope: OperatorScope
