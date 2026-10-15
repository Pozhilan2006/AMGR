from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import resolve_request_scope
from app.db.session import get_db
from app.schemas.flow_event import FlowEvent, FlowEventIngestResponse
from app.services.event_ingestion import (
    EventIngestionService,
    EventPayloadConflictError,
    InvalidParentEventError,
)

router = APIRouter()


@router.post("", response_model=FlowEventIngestResponse)
def ingest_flow_event(
    event: FlowEvent,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> FlowEventIngestResponse:
    """Persist one authorized event and update its observed graph and lineage."""
    scope = resolve_request_scope(request, event.application_id, event.environment)
    service = EventIngestionService()
    try:
        with session.begin():
            result = service.ingest(
                session,
                event,
                application_id=scope.application_id,
                environment=scope.environment,
            )
    except InvalidParentEventError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PARENT_REFERENCE", "message": exc.message},
        ) from exc
    except EventPayloadConflictError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message, "event_id": str(exc.event_id)},
        ) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The event could not be recorded because its ID or graph identity is already in use.",
        ) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    response.status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED
    return result
