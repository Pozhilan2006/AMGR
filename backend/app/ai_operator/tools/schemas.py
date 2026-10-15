from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call: ToolCallRequest | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "empty", "error", "forbidden", "unavailable"]
    message: str = Field(min_length=1, max_length=4000)
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ComponentDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


class FlowLineageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(min_length=1, max_length=200)
    direction: Literal["upstream", "downstream"]
    limit: int = Field(default=10, ge=1, le=50)
    traversal_depth: int = Field(default=2, ge=1, le=8)


class ComponentPathsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_component_id: str = Field(min_length=1, max_length=200)
    destination_component_id: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    traversal_depth: int = Field(default=2, ge=1, le=8)


class PolicyDecisionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str | None = Field(default=None, max_length=200)
    flow_id: str | None = Field(default=None, max_length=200)

    @property
    def lookup_key(self) -> str:
        return self.decision_id or self.flow_id or ""


class PolicyDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=200)


class DecisionHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
