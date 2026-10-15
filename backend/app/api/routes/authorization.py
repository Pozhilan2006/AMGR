"""Authorization API routes module.

Provides compatibility endpoint for `/decisions` delegating directly to
the canonical `AuthorizationService` evaluation logic. Canonical runtime
authorization endpoints are defined in `backend.app.api.authorization`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.app.api.authorization import get_authorization_service
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from backend.app.services.authorization_service import AuthorizationService

router = APIRouter()


@router.post(
    "/decisions",
    response_model=AuthorizationDecision,
    status_code=status.HTTP_200_OK,
    summary="Evaluate authorization decision (compatibility alias for /api/v1/authorize)",
)
def authorize(
    request: AuthorizationRequest,
    response: Response,
    service: AuthorizationService = Depends(get_authorization_service),
) -> AuthorizationDecision:
    """Evaluates an authorization request deterministically using canonical AuthorizationService."""
    try:
        decision, appr_req = service.authorize(request)
        if appr_req is not None:
            response.headers["X-AMGR-Approval-ID"] = appr_req.id
        return decision
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authorization evaluation failed safely.",
        ) from exc