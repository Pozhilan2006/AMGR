from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai_operator.query_schemas import normalize_identifier
from app.ai_operator.schemas import OperatorScope


class LineageQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    direction: str
    lineage: list[dict[str, Any]] = Field(default_factory=list)
    scope: str
    unavailable: bool = False
    message: str | None = None


async def lookup_upstream_lineage(
    component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
    traversal_depth: int = 2,
) -> LineageQueryResult:
    normalized = normalize_identifier(component_id)
    if not normalized:
        raise ValueError("A component identifier is required for upstream lineage lookup.")
    if limit < 1 or traversal_depth < 1:
        raise ValueError("limit and traversal_depth must be positive integers.")

    return LineageQueryResult(
        component_id=normalized,
        direction="upstream",
        lineage=[],
        scope=f"{scope.application_id}:{scope.environment}",
        unavailable=True,
        message=(
            "The lineage service is not implemented in this repository, so no authoritative "
            "upstream lineage records can be retrieved."
        ),
    )


async def lookup_downstream_lineage(
    component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
    traversal_depth: int = 2,
) -> LineageQueryResult:
    normalized = normalize_identifier(component_id)
    if not normalized:
        raise ValueError("A component identifier is required for downstream lineage lookup.")
    if limit < 1 or traversal_depth < 1:
        raise ValueError("limit and traversal_depth must be positive integers.")

    return LineageQueryResult(
        component_id=normalized,
        direction="downstream",
        lineage=[],
        scope=f"{scope.application_id}:{scope.environment}",
        unavailable=True,
        message=(
            "The lineage service is not implemented in this repository, so no authoritative "
            "downstream lineage records can be retrieved."
        ),
    )
