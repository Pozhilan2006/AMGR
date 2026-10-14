"""FastAPI authorization and governance approval API endpoints.

Exposes deterministic runtime authorization evaluation, pending approval retrieval,
explicit human decision submission, and operation permission checks following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.adapter import (
    PolicyIdCompatibilityError,
    SDKAuthorizationDecision,
    SDKAuthorizationRequest,
    convert_backend_decision_to_sdk,
    convert_sdk_request_to_backend,
)
from backend.app.schemas.approval import (
    ApprovalDecisionSubmission,
    ApprovalRequest,
    ApprovalStatus,
)
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService

# Module-level default singleton dependencies
_policy_repo_instance: PolicyRepository | None = None
_approval_service_instance: ApprovalService | None = None
_auth_service_instance: AuthorizationService | None = None


def get_policy_repository() -> PolicyRepository:
    """Dependency provider for PolicyRepository."""
    global _policy_repo_instance
    if _policy_repo_instance is None:
        _policy_repo_instance = PolicyRepository()
    return _policy_repo_instance


def get_approval_service() -> ApprovalService:
    """Dependency provider for ApprovalService."""
    global _approval_service_instance
    if _approval_service_instance is None:
        _approval_service_instance = ApprovalService()
    return _approval_service_instance


def get_authorization_service(
    policy_repo: PolicyRepository = Depends(get_policy_repository),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> AuthorizationService:
    """Dependency provider for AuthorizationService."""
    global _auth_service_instance
    if (
        _auth_service_instance is None
        or _auth_service_instance.policy_repo is not policy_repo
        or _auth_service_instance.approval_service is not approval_service
    ):
        _auth_service_instance = AuthorizationService(
            policy_repo=policy_repo,
            approval_service=approval_service,
        )
    return _auth_service_instance


def reset_services(
    policy_repo: PolicyRepository | None = None,
    approval_service: ApprovalService | None = None,
    auth_service: AuthorizationService | None = None,
) -> None:
    """Resets or overrides the default module-level service instances."""
    global _policy_repo_instance, _approval_service_instance, _auth_service_instance
    _policy_repo_instance = policy_repo
    _approval_service_instance = approval_service
    _auth_service_instance = auth_service


router = APIRouter(tags=["authorization"])


@router.post(
    "/authorize",
    response_model=AuthorizationDecision,
    status_code=status.HTTP_200_OK,
    summary="Evaluate runtime authorization request against governance policies",
)
def authorize(
    request: AuthorizationRequest,
    response: Response,
    service: AuthorizationService = Depends(get_authorization_service),
) -> AuthorizationDecision:
    """Deterministically evaluates an authorization request at the runtime enforcement boundary.

    - Resolves caller identity, action, resource, and execution context attributes.
    - Evaluates against active policies in the repository using deterministic algorithms.
    - Checks for prior approved human authorizations when approval_id is in context.
    - Automatically creates a pending ApprovalRequest if REQUIRE_APPROVAL is rendered.
    - Returns deterministic AuthorizationDecision.
    """
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


@router.post(
    "/enforcement/authorize",
    response_model=SDKAuthorizationDecision,
    status_code=status.HTTP_200_OK,
    summary="SDK-compatible runtime authorization enforcement endpoint",
)
def enforcement_authorize(
    request: SDKAuthorizationRequest,
    response: Response,
    service: AuthorizationService = Depends(get_authorization_service),
) -> SDKAuthorizationDecision:
    """Evaluates an SDK-compatible authorization request at the runtime enforcement boundary.

    - Accepts SDKAuthorizationRequest matching Member 1 SDK contract.
    - Deterministically adapts request to canonical AuthorizationRequest.
    - Evaluates against active policies via AuthorizationService.
    - Persists decision and enforces transactional integrity.
    - Generates pending ApprovalRequest on REQUIRE_APPROVAL and sets X-AMGR-Approval-ID header.
    - Maps decision to SDKAuthorizationDecision with strict policy ID fidelity.
    - Fails safely on errors without leaking internals or returning synthetic ALLOW.
    """
    try:
        canonical_req = convert_sdk_request_to_backend(request)
        decision, appr_req = service.authorize(canonical_req)
        if appr_req is not None:
            response.headers["X-AMGR-Approval-ID"] = appr_req.id
        return convert_backend_decision_to_sdk(
            decision=decision,
            request_id=request.request_id,
        )
    except HTTPException:
        raise
    except PolicyIdCompatibilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Policy-ID contract incompatibility: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authorization evaluation failed safely.",
        ) from exc



@router.get(
    "/approvals",
    response_model=list[ApprovalRequest],
    status_code=status.HTTP_200_OK,
    summary="List governance approval requests",
)
def list_approvals(
    status_filter: ApprovalStatus | None = Query(
        default=None,
        alias="status",
        description="Filter requests by approval lifecycle status",
    ),
    service: ApprovalService = Depends(get_approval_service),
) -> list[ApprovalRequest]:
    """Retrieves approval requests, optionally filtered by status."""
    if status_filter == ApprovalStatus.PENDING:
        return service.list_pending_requests()
    elif status_filter is not None:
        return [r for r in service.list_all_requests() if r.status == status_filter]
    return service.list_all_requests()


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalRequest,
    status_code=status.HTTP_200_OK,
    summary="Retrieve governance approval request details",
)
def get_approval(
    approval_id: str,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequest:
    """Retrieves an approval request by its unique identifier."""
    req = service.get_approval_request(approval_id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{approval_id}' not found.",
        )
    return req


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRequest,
    status_code=status.HTTP_200_OK,
    summary="Submit explicit human approval or rejection decision",
)
def submit_approval_decision(
    approval_id: str,
    submission: ApprovalDecisionSubmission,
    service: ApprovalService = Depends(get_approval_service),
    policy_repo: PolicyRepository = Depends(get_policy_repository),
) -> ApprovalRequest:
    """Records an explicit human reviewer decision (APPROVE or REJECT) for an approval request.

    Enforces:
    - AI / LLM entities are forbidden from resolving requests (403 Forbidden).
    - Reviewer must be in authorized approvers registry if configured (403 Forbidden).
    - Single-decision finality: cannot overwrite finalized decisions (409 Conflict).
    - Policy version drift protection: invalidates approval if policy updated (409 Conflict).
    """
    try:
        return service.submit_decision(
            approval_id=approval_id,
            submission=submission,
            policy_repo=policy_repo,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get(
    "/approvals/{approval_id}/can-proceed",
    status_code=status.HTTP_200_OK,
    summary="Check if a protected operation is authorized to proceed",
)
def check_can_proceed(
    approval_id: str,
    service: ApprovalService = Depends(get_approval_service),
    policy_repo: PolicyRepository = Depends(get_policy_repository),
) -> dict[str, Any]:
    """Determines whether a protected operation associated with an approval request can proceed.

    Fails closed (returns can_proceed=False) if pending, rejected, expired,
    or if underlying policy revision has mutated or become deactivated.
    """
    req = service.get_approval_request(approval_id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{approval_id}' not found.",
        )
    can_proceed = service.can_proceed(approval_id, policy_repo=policy_repo)
    return {
        "approval_id": approval_id,
        "can_proceed": can_proceed,
        "status": req.status.value,
        "final_effect": req.final_effect.value,
    }
