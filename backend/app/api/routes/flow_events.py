from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.flow_event import (
    FlowEvent,
    FlowEventIngestionResponse,
    IngestionStatus,
)
from app.services.event_ingestion import (
    EventIngestionService,
    EventPayloadConflictError,
)

router = APIRouter()

_ingestion_service = EventIngestionService()


def get_ingestion_service() -> EventIngestionService:
    return _ingestion_service


@router.post(
    "",
    response_model=FlowEventIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": FlowEventIngestionResponse,
            "description": "Identical event previously committed (idempotent retry).",
        },
        status.HTTP_409_CONFLICT: {
            "description": "Event ID collision with conflicting payload content.",
        },
    },
)
def ingest_flow_event(
    event: FlowEvent,
    response: Response,
    session: Session = Depends(get_db),
    ingestion_service: EventIngestionService = Depends(get_ingestion_service),
) -> FlowEventIngestionResponse:
    """Ingest a runtime flow event, persisting the event record and updating the flow graph.

    Outcomes:
        - 201 Created: New event persisted and flow graph updated.
        - 200 OK: Identical event already persisted; returned idempotently without updating graph.
        - 409 Conflict: Same event_id submitted with conflicting payload attributes.
    """
    try:
        with session.begin():
            result = ingestion_service.ingest(session, event)
            if result.status == IngestionStatus.ALREADY_PROCESSED:
                response.status_code = status.HTTP_200_OK
            else:
                response.status_code = status.HTTP_201_CREATED
            return result
    except EventPayloadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": exc.message,
                "event_id": str(exc.event_id),
            },
        ) from exc