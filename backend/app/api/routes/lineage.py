from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import resolve_request_scope
from app.db.session import get_db
from app.repositories.lineage_repository import LineageRepository
from app.schemas.lineage import LineageDirection, LineageResponse
from app.services.lineage_service import LineageService

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/events/{event_id}", response_model=LineageResponse)
def get_event_lineage(
    event_id: UUID,
    request: Request,
    session: DbSession,
    direction: Literal["upstream", "downstream", "both"] = "both",
    max_depth: Annotated[int, Query(ge=1, le=8)] = 3,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> LineageResponse:
    scope = resolve_request_scope(request, application_id, environment)
    result = LineageService(LineageRepository(session)).resolve_event_lineage(
        event_id,
        scope=scope,
        direction=direction,
        max_depth=max_depth,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found in the authorized scope.")
    return result


@router.get("/flows/{flow_id}", response_model=LineageResponse)
def get_flow_lineage(
    flow_id: UUID,
    request: Request,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> LineageResponse:
    scope = resolve_request_scope(request, application_id, environment)
    return LineageService(LineageRepository(session)).resolve_flow_lineage(
        flow_id, scope=scope, limit=limit, offset=offset
    )


@router.get("/components/{component_id}", response_model=LineageResponse)
def get_component_lineage(
    component_id: Annotated[str, Path(min_length=1, max_length=200)],
    request: Request,
    session: DbSession,
    direction: LineageDirection = "both",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    application_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> LineageResponse:
    scope = resolve_request_scope(request, application_id, environment)
    return LineageService(LineageRepository(session)).resolve_component_lineage(
        component_id,
        scope=scope,
        direction=direction,
        limit=limit,
        offset=offset,
    )
