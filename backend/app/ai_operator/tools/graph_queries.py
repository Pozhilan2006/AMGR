from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai_operator.query_schemas import normalize_identifier
from app.ai_operator.schemas import OperatorScope


class GraphQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    neighbors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    scope: str
    unavailable: bool = False
    message: str | None = None


class ExternalServiceQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    scope: str
    unavailable: bool = False
    message: str | None = None


def _scope_label(scope: OperatorScope) -> str:
    return f"{scope.application_id}:{scope.environment}"


async def lookup_component_neighbors(
    component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
) -> GraphQueryResult:
    normalized = normalize_identifier(component_id)
    if not normalized:
        raise ValueError("A component identifier is required for component lookup.")

    if limit < 1:
        raise ValueError("limit must be at least 1.")

    message = (
        "The graph API is not implemented in this repository; no authoritative graph "
        "edge data is available for component lookup."
    )
    return GraphQueryResult(
        component_id=normalized,
        neighbors=[],
        metadata={"limit": limit},
        scope=_scope_label(scope),
        unavailable=True,
        message=message,
    )


async def lookup_path(
    source_component_id: str | None,
    destination_component_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
    traversal_depth: int = 2,
) -> list[str]:
    source = normalize_identifier(source_component_id)
    destination = normalize_identifier(destination_component_id)
    if not source or not destination:
        raise ValueError("Both source and destination component identifiers are required.")
    if limit < 1 or traversal_depth < 1:
        raise ValueError("limit and traversal_depth must be positive integers.")

    return []


async def lookup_external_service_interactions(
    service_name: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
) -> ExternalServiceQueryResult:
    normalized = normalize_identifier(service_name)
    if not normalized:
        raise ValueError("A service name is required for external-service interaction lookup.")

    if limit < 1:
        raise ValueError("limit must be at least 1.")

    message = (
        "No external service interaction records are currently exposed by the repository's "
        "implemented interfaces."
    )
    return ExternalServiceQueryResult(
        service_name=normalized,
        interactions=[],
        scope=_scope_label(scope),
        unavailable=True,
        message=message,
    )
