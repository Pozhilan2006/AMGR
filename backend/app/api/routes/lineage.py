"""Lineage and provenance API routes for AMGR.

Provides REST endpoints for querying single-event provenance, flow lineage,
component-level interactions, and DAG traversal (upstream/downstream blast radius)
following explicit parent-event relationships with strict environment and
application boundary scoping.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

try:
    from backend.app.core.config import settings
    from backend.app.db.session import get_db
    from backend.app.services.lineage_service import LineageService
except ImportError:
    from app.core.config import settings
    from app.db.session import get_db
    from app.services.lineage_service import LineageService

ENVIRONMENT_MAX_LENGTH: int = 64
APPLICATION_ID_MAX_LENGTH: int = 255

router = APIRouter()

_lineage_service = LineageService()


def get_lineage_service() -> LineageService:
    """Dependency provider for LineageService singleton."""
    return _lineage_service


def _resolve_scope(
    x_amgr_environment: str | None = None,
    x_amgr_application_id: str | None = None,
    environment: str | None = None,
    application_id: str | None = None,
) -> tuple[str, str | None]:
    """Resolve and validate deployment environment and application boundary scopes.

    Header values take precedence over query parameters. If neither header nor query
    specifies an environment, defaults to settings.environment.
    """
    raw_env = (x_amgr_environment or environment or "").strip()
    if raw_env:
        if len(raw_env) > ENVIRONMENT_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Environment identifier exceeds maximum allowed length of {ENVIRONMENT_MAX_LENGTH} characters.",
            )
        effective_env = raw_env
    else:
        effective_env = settings.environment

    raw_app = (x_amgr_application_id or application_id or "").strip()
    if raw_app:
        if len(raw_app) > APPLICATION_ID_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Application identifier exceeds maximum allowed length of {APPLICATION_ID_MAX_LENGTH} characters.",
            )
        effective_app = raw_app
    else:
        effective_app = None

    return effective_env, effective_app


@router.get(
    "/events/{event_id}",
    summary="Get event lineage or traverse provenance DAG",
    responses={
        status.HTTP_200_OK: {
            "description": "Event provenance or traversal DAG successfully retrieved.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Event not found within the requested scope.",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Invalid event UUID, parameter boundary violation, or unsupported direction.",
        },
    },
)
def get_event_lineage(
    event_id: str,
    direction: str | None = Query(
        default=None,
        description="Optional traversal direction: 'upstream' (ancestors) or 'downstream' (descendants). If omitted, returns direct single-event provenance.",
    ),
    max_depth: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum traversal depth (1 to 100). Applicable only when direction is specified.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of events/hops to return (1 to 1000). Applicable only when direction is specified.",
    ),
    x_amgr_environment: str | None = Header(
        default=None,
        alias="X-AMGR-Environment",
        description="Optional deployment environment scope header.",
    ),
    x_amgr_application_id: str | None = Header(
        default=None,
        alias="X-AMGR-Application-ID",
        description="Optional application boundary header.",
    ),
    environment: str | None = Query(
        default=None,
        description="Optional deployment environment scope query parameter.",
    ),
    application_id: str | None = Query(
        default=None,
        description="Optional application boundary query parameter.",
    ),
    session: Session = Depends(get_db),
    service: LineageService = Depends(get_lineage_service),
) -> dict[str, Any]:
    """Retrieve direct provenance for a single event, or traverse upstream/downstream along explicit parent links.

    When direction is omitted:
        Returns direct LineageHop including the event, associated lineage record,
        origin and target GraphNodes (if within scope), and GraphEdge.

    When direction is 'upstream' or 'downstream':
        Executes bounded DAG traversal following explicit parent_event_id links,
        enforcing depth/result limits and returning traversal completeness metadata.
    """
    try:
        parsed_event_id = UUID(event_id.strip())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid event UUID: '{event_id}'.",
        )

    effective_env, effective_app = _resolve_scope(
        x_amgr_environment=x_amgr_environment,
        x_amgr_application_id=x_amgr_application_id,
        environment=environment,
        application_id=application_id,
    )

    if direction is None:
        hop = service.get_event_lineage(
            session,
            parsed_event_id,
            environment=effective_env,
            application_id=effective_app,
        )
        if hop is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lineage event '{event_id}' not found within requested scope.",
            )
        return hop.to_dict()

    normalized_direction = direction.lower().strip()
    if normalized_direction not in ("upstream", "downstream"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported traversal direction: '{direction}'. Must be 'upstream' or 'downstream'.",
        )

    if normalized_direction == "downstream":
        traversal = service.traverse_downstream(
            session,
            parsed_event_id,
            environment=effective_env,
            application_id=effective_app,
            max_depth=max_depth,
            limit=limit,
        )
    else:
        traversal = service.traverse_upstream(
            session,
            parsed_event_id,
            environment=effective_env,
            application_id=effective_app,
            max_depth=max_depth,
            limit=limit,
        )

    if traversal.metadata.total_count == 0 and not traversal.events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target event '{event_id}' not found within requested scope.",
        )

    return traversal.to_dict()


@router.get(
    "/events/{event_id}/traverse",
    summary="Traverse event provenance DAG (upstream or downstream)",
    responses={
        status.HTTP_200_OK: {
            "description": "Traversal DAG successfully retrieved.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Target event not found within the requested scope.",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Invalid event UUID, parameter boundary violation, or unsupported direction.",
        },
    },
)
def traverse_event_lineage(
    event_id: str,
    direction: str = Query(
        default="upstream",
        description="Traversal direction: 'upstream' (ancestors towards root) or 'downstream' (descendants / blast radius).",
    ),
    flow_id: str | None = Query(
        default=None,
        description="Optional flow session UUID constraint.",
    ),
    max_depth: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum traversal depth (1 to 100).",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of events/hops to return (1 to 1000).",
    ),
    x_amgr_environment: str | None = Header(
        default=None,
        alias="X-AMGR-Environment",
        description="Optional deployment environment scope header.",
    ),
    x_amgr_application_id: str | None = Header(
        default=None,
        alias="X-AMGR-Application-ID",
        description="Optional application boundary header.",
    ),
    environment: str | None = Query(
        default=None,
        description="Optional deployment environment scope query parameter.",
    ),
    application_id: str | None = Query(
        default=None,
        description="Optional application boundary query parameter.",
    ),
    session: Session = Depends(get_db),
    service: LineageService = Depends(get_lineage_service),
) -> dict[str, Any]:
    """Compatible dedicated endpoint for upstream and downstream causal DAG traversal."""
    try:
        parsed_event_id = UUID(event_id.strip())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid event UUID: '{event_id}'.",
        )

    parsed_flow_id: UUID | None = None
    if flow_id is not None:
        try:
            parsed_flow_id = UUID(flow_id.strip())
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid flow UUID: '{flow_id}'.",
            )

    effective_env, effective_app = _resolve_scope(
        x_amgr_environment=x_amgr_environment,
        x_amgr_application_id=x_amgr_application_id,
        environment=environment,
        application_id=application_id,
    )

    normalized_direction = direction.lower().strip()
    if normalized_direction not in ("upstream", "downstream"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported traversal direction: '{direction}'. Must be 'upstream' or 'downstream'.",
        )

    if normalized_direction == "downstream":
        traversal = service.traverse_downstream(
            session,
            parsed_event_id,
            flow_id=parsed_flow_id,
            environment=effective_env,
            application_id=effective_app,
            max_depth=max_depth,
            limit=limit,
        )
    else:
        traversal = service.traverse_upstream(
            session,
            parsed_event_id,
            flow_id=parsed_flow_id,
            environment=effective_env,
            application_id=effective_app,
            max_depth=max_depth,
            limit=limit,
        )

    if traversal.metadata.total_count == 0 and not traversal.events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target event '{event_id}' not found within requested scope.",
        )

    return traversal.to_dict()


@router.get(
    "/flows/{flow_id}",
    summary="Get paginated lineage records for a flow",
    responses={
        status.HTTP_200_OK: {
            "description": "Flow lineage records successfully retrieved.",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Invalid flow UUID or parameter boundary violation.",
        },
    },
)
def get_flow_lineage(
    flow_id: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of records to return (1 to 1000).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of records to skip.",
    ),
    x_amgr_environment: str | None = Header(
        default=None,
        alias="X-AMGR-Environment",
        description="Optional deployment environment scope header.",
    ),
    x_amgr_application_id: str | None = Header(
        default=None,
        alias="X-AMGR-Application-ID",
        description="Optional application boundary header.",
    ),
    environment: str | None = Query(
        default=None,
        description="Optional deployment environment scope query parameter.",
    ),
    application_id: str | None = Query(
        default=None,
        description="Optional application boundary query parameter.",
    ),
    session: Session = Depends(get_db),
    service: LineageService = Depends(get_lineage_service),
) -> dict[str, Any]:
    """Retrieve chronologically ordered lineage records for a runtime flow session.

    Enforces environment and application scoping, and returns pagination metadata
    along with completeness flag (is_truncated).
    """
    try:
        parsed_flow_id = UUID(flow_id.strip())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid flow UUID: '{flow_id}'.",
        )

    effective_env, effective_app = _resolve_scope(
        x_amgr_environment=x_amgr_environment,
        x_amgr_application_id=x_amgr_application_id,
        environment=environment,
        application_id=application_id,
    )

    result = service.get_flow_lineage(
        session,
        parsed_flow_id,
        environment=effective_env,
        application_id=effective_app,
        limit=limit,
        offset=offset,
    )
    return result.to_dict()


@router.get(
    "/components/{component_id}",
    summary="Get events and provenance associated with a component",
    responses={
        status.HTTP_200_OK: {
            "description": "Component events and provenance successfully retrieved.",
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Empty component identifier or parameter boundary violation.",
        },
    },
)
def get_component_lineage(
    component_id: str,
    flow_id: str | None = Query(
        default=None,
        description="Optional flow session UUID constraint.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of records to return (1 to 1000).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of records to skip.",
    ),
    x_amgr_environment: str | None = Header(
        default=None,
        alias="X-AMGR-Environment",
        description="Optional deployment environment scope header.",
    ),
    x_amgr_application_id: str | None = Header(
        default=None,
        alias="X-AMGR-Application-ID",
        description="Optional application boundary header.",
    ),
    environment: str | None = Query(
        default=None,
        description="Optional deployment environment scope query parameter.",
    ),
    application_id: str | None = Query(
        default=None,
        description="Optional application boundary query parameter.",
    ),
    session: Session = Depends(get_db),
    service: LineageService = Depends(get_lineage_service),
) -> dict[str, Any]:
    """Retrieve events, lineage records, nodes, and edges associated with a component.

    Supports matching by internal node UUID or external component ID through
    scoped node queries, with pagination and environment/application boundaries.
    """
    stripped_id = component_id.strip() if component_id is not None else ""
    if not stripped_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Component identifier must not be empty.",
        )

    parsed_flow_id: UUID | None = None
    if flow_id is not None:
        try:
            parsed_flow_id = UUID(flow_id.strip())
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid flow UUID: '{flow_id}'.",
            )

    effective_env, effective_app = _resolve_scope(
        x_amgr_environment=x_amgr_environment,
        x_amgr_application_id=x_amgr_application_id,
        environment=environment,
        application_id=application_id,
    )

    result = service.get_component_events(
        session,
        stripped_id,
        environment=effective_env,
        application_id=effective_app,
        flow_id=parsed_flow_id,
        limit=limit,
        offset=offset,
    )
    return result.to_dict()
