from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal[
        "component",
        "event",
        "flow",
        "lineage",
        "policy",
        "decision",
        "capability",
        "unknown",
    ]
    record_id: str | None = Field(default=None, max_length=200)
    source_service: str = Field(default="amgr", min_length=1, max_length=200)
    timestamp: datetime | None = None
    relationship_type: str | None = Field(default=None, max_length=200)
    observation_status: Literal["observed", "derived", "incomplete", "unavailable"] = "observed"
    summary: str = Field(min_length=1, max_length=2000)
