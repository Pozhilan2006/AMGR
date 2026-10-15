from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import resolve_request_scope
from app.db.session import get_db
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph_queries import GraphNeighborhoodResponse, GraphPathResponse, GraphSnapshotResponse
from app.services.graph_service import GraphService

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=GraphSnapshotResponse)
def get_graph(
    request: Request,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    component_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    flow_id: UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    action: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    data_label: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> GraphSnapshotResponse:
    scope = resolve_request_scope(request, application_id, environment)
    try:
        return GraphService(GraphRepository(session)).get_snapshot(
            scope=scope,
            limit=limit,
            offset=offset,
            component_type=component_type,
            flow_id=flow_id,
            start=start,
            end=end,
            action=action,
            data_label=data_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/components/{component_id}/neighborhood", response_model=GraphNeighborhoodResponse)
def get_component_neighborhood(
    component_id: str,
    request: Request,
    session: DbSession,
    direction: Literal["incoming", "outgoing", "both"] = "both",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> GraphNeighborhoodResponse:
    scope = resolve_request_scope(request, application_id, environment)
    try:
        return GraphService(GraphRepository(session)).get_neighborhood(
            component_id=component_id,
            scope=scope,
            direction=direction,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/paths", response_model=GraphPathResponse)
def get_component_paths(
    request: Request,
    session: DbSession,
    source_component_id: Annotated[str, Query(min_length=1, max_length=200)],
    destination_component_id: Annotated[str, Query(min_length=1, max_length=200)],
    max_depth: Annotated[int, Query(ge=1, le=8)] = 2,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> GraphPathResponse:
    scope = resolve_request_scope(request, application_id, environment)
    try:
        return GraphService(GraphRepository(session)).get_paths(
            source_component_id=source_component_id,
            destination_component_id=destination_component_id,
            scope=scope,
            max_depth=max_depth,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc