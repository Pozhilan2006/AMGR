from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.schemas.flow_event import ComponentRef


class GraphNode(BaseModel):
    id: str
    component: ComponentRef
    label: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    action: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))