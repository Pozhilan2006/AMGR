from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

try:
    from backend.app.core.config import settings
    from backend.app.db.session import get_db
    from backend.app.schemas.flow_event import (
        FlowEvent,
        FlowEventIngestionResponse,
        IngestionStatus,
    )
    from backend.app.services.event_ingestion import (
        EventIngestionService,
        EventPayloadConflictError,
        InvalidParentEventError,
    )
except ImportError:
    from app.core.config import settings
    from app.db.session import get_db
    from app.schemas.flow_event import (
        FlowEvent,
        FlowEventIngestionResponse,
        IngestionStatus,
    )
    from app.services.event_ingestion import (
        EventIngestionService,
        EventPayloadConflictError,
        InvalidParentEventError,
    )

ENVIRONMENT_MAX_LENGTH: int = 64
APPLICATION_ID_MAX_LENGTH: int = 255

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
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Scope header length limit exceeded, invalid parent reference, or schema validation failure.",
        },
    },
)
def ingest_flow_event(
    event: FlowEvent,
    response: Response,
    x_amgr_environment: str | None = Header(
        default=None,
        alias="X-AMGR-Environment",
        description="Optional deployment environment scope (defaults to configured backend environment).",
    ),
    x_amgr_application_id: str | None = Header(
        default=None,
        alias="X-AMGR-Application-ID",
        description="Optional application boundary identifier.",
    ),
    session: Session = Depends(get_db),
    ingestion_service: EventIngestionService = Depends(get_ingestion_service),
) -> FlowEventIngestionResponse:
    """Ingest a runtime flow event, persisting the event record and updating the flow graph.

    Outcomes:
        - 201 Created: New event persisted and flow graph updated.
        - 200 OK: Identical event already persisted; returned idempotently without updating graph.
        - 409 Conflict: Same event_id submitted with conflicting payload attributes.
        - 422 Unprocessable Entity: Environment or application ID exceeds column length limits,
          or parent event reference is invalid (self-referential, cyclic, or cross-flow).
    """
    # Scope resolution: strip whitespace, validate length, and apply defaults
    raw_env = x_amgr_environment.strip() if x_amgr_environment is not None else ""
    if raw_env:
        if len(raw_env) > ENVIRONMENT_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"X-AMGR-Environment header exceeds maximum allowed length of {ENVIRONMENT_MAX_LENGTH} characters.",
            )
        effective_env = raw_env
    else:
        effective_env = settings.environment

    raw_app = x_amgr_application_id.strip() if x_amgr_application_id is not None else ""
    if raw_app:
        if len(raw_app) > APPLICATION_ID_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"X-AMGR-Application-ID header exceeds maximum allowed length of {APPLICATION_ID_MAX_LENGTH} characters.",
            )
        effective_app = raw_app
    else:
        effective_app = None

    try:
        with session.begin():
            result = ingestion_service.ingest(
                session,
                event,
                environment=effective_env,
                application_id=effective_app,
            )
            if result.status == IngestionStatus.ALREADY_PROCESSED:
                response.status_code = status.HTTP_200_OK
            else:
                response.status_code = status.HTTP_201_CREATED
            return result
    except InvalidParentEventError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_PARENT_REFERENCE",
                "message": exc.message,
            },
        ) from exc
    except EventPayloadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": exc.message,
                "event_id": str(exc.event_id),
            },
        ) from exc