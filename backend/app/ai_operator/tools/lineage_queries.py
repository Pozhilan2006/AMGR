from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.ai_operator.schemas import OperatorScope
from app.db.session import SessionLocal
from app.repositories.lineage_repository import LineageRepository
from app.schemas.lineage import LineageDirection
from app.services.lineage_service import LineageService


class LineageQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    direction: str
    lineage: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    scope: str
    completeness: str = "unknown"
    truncated: bool = False
    unresolved_parent_event_ids: list[str] = Field(default_factory=list)
    unavailable: bool = False
    message: str | None = None


def _lookup_component_lineage(
    component_id: str,
    *,
    scope: OperatorScope,
    direction: LineageDirection,
    limit: int,
    traversal_depth: int,
) -> LineageQueryResult:
    with SessionLocal() as session:
        response = LineageService(LineageRepository(session)).resolve_component_lineage(
            component_id,
            scope=scope,
            direction=direction,  # validated against the tool contract below
            limit=limit,
            offset=0,
        )
    return LineageQueryResult(
        component_id=component_id,
        direction=direction,
        lineage=[event.model_dump(mode="json") for event in response.events],
        edges=[edge.model_dump(mode="json") for edge in response.edges],
        scope=f"{scope.application_id}:{scope.environment}",
        completeness=response.completeness,
        truncated=response.truncated,
        unresolved_parent_event_ids=response.unresolved_parent_event_ids,
        message=(
            "Component lookup returns one-hop recorded interactions. The requested traversal "
            f"depth ({traversal_depth}) is not applied because component interactions alone "
            "do not establish causality."
        ),
    )


async def _lookup(
    component_id: str | None,
    *,
    direction: Literal["upstream", "downstream"],
    scope: OperatorScope,
    limit: int,
    traversal_depth: int,
) -> LineageQueryResult:
    normalized = component_id.strip() if component_id else ""
    if not normalized:
        raise ValueError("A component identifier is required for lineage lookup.")
    if not 1 <= limit <= 50 or not 1 <= traversal_depth <= 8:
        raise ValueError("limit must be 1-50 and traversal_depth must be 1-8.")
    if direction not in {"upstream", "downstream"}:
        raise ValueError("direction must be upstream or downstream.")

    try:
        return await asyncio.to_thread(
            _lookup_component_lineage,
            normalized,
            scope=scope,
            direction=direction,
            limit=limit,
            traversal_depth=traversal_depth,
        )
    except SQLAlchemyError:
        return LineageQueryResult(
            component_id=normalized,
            direction=direction,
            scope=f"{scope.application_id}:{scope.environment}",
            unavailable=True,
            message="Stored lineage records are unavailable.",
        )


async def lookup_upstream_lineage(
    component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
    traversal_depth: int = 2,
) -> LineageQueryResult:
    return await _lookup(
        component_id,
        direction="upstream",
        scope=scope,
        limit=limit,
        traversal_depth=traversal_depth,
    )


async def lookup_downstream_lineage(
    component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
    traversal_depth: int = 2,
) -> LineageQueryResult:
    return await _lookup(
        component_id,
        direction="downstream",
        scope=scope,
        limit=limit,
        traversal_depth=traversal_depth,
    )
