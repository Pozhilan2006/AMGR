from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import resolve_request_scope
from app.db.session import get_db
from app.repositories.lineage_repository import LineageRepository
from app.schemas.flow_event import FlowEvent, FlowEventIngestResponse
from app.services.event_ingestion import EventIngestionService

router = APIRouter()


@router.post("", response_model=FlowEventIngestResponse)
def ingest_flow_event(
    event: FlowEvent,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> FlowEventIngestResponse:
    resolve_request_scope(request, event.application_id, event.environment)
    service = EventIngestionService(LineageRepository(session))
    try:
        created = service.ingest(event)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An event with this ID already exists with different content.",
        ) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The event could not be recorded because its ID is already in use.",
        ) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return FlowEventIngestResponse(event_id=event.event_id, duplicate=not created)