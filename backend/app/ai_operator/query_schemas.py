from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvestigationIntentType(str, Enum):
    COMPONENT_LOOKUP = "component_lookup"
    UPSTREAM_LINEAGE = "upstream_lineage"
    DOWNSTREAM_LINEAGE = "downstream_lineage"
    PATH_LOOKUP = "path_lookup"
    EXTERNAL_SERVICE_INTERACTION = "external_service_interaction"
    POLICY_DECISION_EXPLANATION = "policy_decision_explanation"
    EVENT_HISTORY_LOOKUP = "event_history_lookup"
    AMGR_CAPABILITY_EXPLANATION = "amgr_capability_explanation"


class InvestigationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: InvestigationIntentType
    component_id: str | None = Field(default=None, max_length=200)
    source_component_id: str | None = Field(default=None, max_length=200)
    destination_component_id: str | None = Field(default=None, max_length=200)
    flow_id: str | None = Field(default=None, max_length=200)
    event_id: str | None = Field(default=None, max_length=200)
    decision_id: str | None = Field(default=None, max_length=200)
    direction: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)
    traversal_depth: int = Field(default=2, ge=1, le=8)
    clarification_required: bool = False
    clarification_question: str | None = None

    @field_validator("component_id", "source_component_id", "destination_component_id", "flow_id", "event_id", "decision_id")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


class InvestigationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_type: str = Field(min_length=1, max_length=50)
    reference_id: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    timestamp: datetime | None = None


class InvestigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=12000)
    intent_type: InvestigationIntentType
    evidence: list[InvestigationEvidence] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_question: str | None = None
    limitations: list[str] = Field(default_factory=list)


def normalize_identifier(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    identifier = identifier.strip()
    return identifier or None
