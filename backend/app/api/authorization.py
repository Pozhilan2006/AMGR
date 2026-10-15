"""FastAPI authorization and governance approval API endpoints.

Exposes deterministic runtime authorization evaluation, pending approval retrieval,
explicit human decision submission, and operation permission checks following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.app.repositories.approval_repository import (
    ApprovalPersistenceError,
    ApprovalRepository,
)
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.adapter import (
    PolicyIdCompatibilityError,
    SDKAuthorizationDecision,
    SDKAuthorizationRequest,
    convert_backend_decision_to_sdk,
    convert_sdk_request_to_backend,
)
from backend.app.schemas.approval import (
    ApprovalCancellationSubmission,
    ApprovalDecisionSubmission,
    ApprovalRequest,
    ApprovalStatus,
)
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    DecisionPurgeRequest,
    DecisionPurgeResponse,
    DecisionRecord,
    DecisionSummary,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService

# Module-level default singleton dependencies
_policy_repo_instance: PolicyRepository | None = None
_approval_repo_instance: ApprovalRepository | None = None
_approval_service_instance: ApprovalService | None = None
_decision_repo_instance: DecisionRepository | None = None
_auth_service_instance: AuthorizationService | None = None


def get_policy_repository() -> PolicyRepository:
    """Dependency provider for PolicyRepository."""
    global _policy_repo_instance
    if _policy_repo_instance is None:
        _policy_repo_instance = PolicyRepository()
    return _policy_repo_instance


def get_approval_repository() -> ApprovalRepository:
    """Dependency provider for ApprovalRepository."""
    global _approval_repo_instance
    if _approval_repo_instance is None:
        _approval_repo_instance = ApprovalRepository()
    return _approval_repo_instance


def get_approval_service(
    approval_repo: ApprovalRepository = Depends(get_approval_repository),
) -> ApprovalService:
    """Dependency provider for ApprovalService."""
    global _approval_service_instance
    if (
        _approval_service_instance is None
        or _approval_service_instance.repository is not approval_repo
    ):
        _approval_service_instance = ApprovalService(repository=approval_repo)
    return _approval_service_instance


def get_decision_repository() -> DecisionRepository:
    """Dependency provider for DecisionRepository."""
    global _decision_repo_instance
    if _decision_repo_instance is None:
        _decision_repo_instance = DecisionRepository()
    return _decision_repo_instance


def get_authorization_service(
    policy_repo: PolicyRepository = Depends(get_policy_repository),
    approval_service: ApprovalService = Depends(get_approval_service),
    decision_repo: DecisionRepository = Depends(get_decision_repository),
) -> AuthorizationService:
    """Dependency provider for AuthorizationService."""
    global _auth_service_instance
    if (
        _auth_service_instance is None
        or _auth_service_instance.policy_repo is not policy_repo
        or _auth_service_instance.approval_service is not approval_service
        or _auth_service_instance.decision_repo is not decision_repo
    ):
        _auth_service_instance = AuthorizationService(
            policy_repo=policy_repo,
            approval_service=approval_service,
            decision_repo=decision_repo,
        )
    return _auth_service_instance


def reset_services(
    policy_repo: PolicyRepository | None = None,
    approval_service: ApprovalService | None = None,
    auth_service: AuthorizationService | None = None,
    decision_repo: DecisionRepository | None = None,
    approval_repo: ApprovalRepository | None = None,
) -> None:
    """Resets or overrides the default module-level service instances."""
    global _policy_repo_instance, _approval_repo_instance, _approval_service_instance, _auth_service_instance, _decision_repo_instance
    _policy_repo_instance = policy_repo
    _approval_repo_instance = approval_repo
    _approval_service_instance = approval_service
    _auth_service_instance = auth_service
    _decision_repo_instance = decision_repo


router = APIRouter(tags=["authorization"])


@router.post(
    "/authorize",
    response_model=AuthorizationDecision,
    status_code=status.HTTP_200_OK,
    summary="Evaluate runtime authorization request against governance policies",
)
@router.post(
    "/authorization",
    response_model=AuthorizationDecision,
    status_code=status.HTTP_200_OK,
    summary="Evaluate runtime authorization request against governance policies (alias)",
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
    trace_id: str | None = Query(
        default=None,
        description="Filter requests by correlation flow trace identifier",
    ),
    policy_id: str | None = Query(
        default=None,
        description="Filter requests by associated policy identifier",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum items to return"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    service: ApprovalService = Depends(get_approval_service),
) -> list[ApprovalRequest]:
    """Retrieves approval requests, with multi-parameter filtering and pagination."""
    return service.list_approvals(
        status_filter=status_filter,
        trace_id=trace_id,
        policy_id=policy_id,
        limit=limit,
        offset=offset,
    )



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


@router.post(
    "/approvals/{approval_id}/cancel",
    response_model=ApprovalRequest,
    status_code=status.HTTP_200_OK,
    summary="Cancel or invalidate a pending or approved approval request",
)
def cancel_approval_request(
    approval_id: str,
    submission: ApprovalCancellationSubmission,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequest:
    """Cancels an approval request, transitioning its lifecycle state to EXPIRED with BLOCK outcome.

    Enforces:
    - Automated AI / LLM entities cannot cancel approvals (403 Forbidden).
    - Request must exist (404 Not Found).
    - Request must not already be in a terminal rejected or expired state (409 Conflict).
    """
    try:
        return service.cancel_approval(
            approval_id=approval_id,
            actor=submission.actor,
            reason=submission.reason,
            is_ai_or_llm=submission.is_ai_or_llm,
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


@router.post(
    "/approvals/expire-stale",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Trigger maintenance sweep to expire requests past their TTL",
)
def sweep_stale_approvals(
    service: ApprovalService = Depends(get_approval_service),
) -> dict[str, Any]:
    """Expires all pending or approved approval requests that have passed their TTL."""
    expired = service.expire_stale_requests()
    return {"expired_count": len(expired), "expired_ids": expired}


@router.get(
    "/decisions",
    response_model=list[DecisionRecord],
    status_code=status.HTTP_200_OK,
    summary="List runtime authorization evaluation decisions from history",
)
def list_decisions(
    trace_id: str | None = Query(default=None, description="Filter decisions by trace correlation identifier"),
    policy_id: str | None = Query(default=None, description="Filter decisions by deciding policy ID"),
    decision: str | None = Query(default=None, description="Filter decisions by outcome (ALLOW, BLOCK, REQUIRE_APPROVAL)"),
    action: str | None = Query(default=None, description="Filter decisions by operation action"),
    resource: str | None = Query(default=None, description="Filter decisions by target resource"),
    limit: int = Query(default=50, ge=1, le=1000, description="Maximum number of decisions to return"),
    offset: int = Query(default=0, ge=0, description="Number of decisions to skip for pagination"),
    decision_repo: DecisionRepository = Depends(get_decision_repository),
) -> list[DecisionRecord]:
    """Retrieves evaluated decision history records with multi-attribute filtering and pagination."""
    raw_decisions = decision_repo.list_decisions(
        trace_id=trace_id,
        policy_id=policy_id,
        decision=decision,
        action=action,
        resource=resource,
        limit=limit,
        offset=offset,
    )
    return [DecisionRecord.model_validate(d) for d in raw_decisions]


@router.get(
    "/decisions/summary",
    response_model=DecisionSummary,
    status_code=status.HTTP_200_OK,
    summary="Get aggregated metrics and statistical summary of evaluated decisions",
)
def get_decision_summary(
    trace_id: str | None = Query(default=None, description="Filter decisions by trace correlation identifier"),
    policy_id: str | None = Query(default=None, description="Filter decisions by deciding policy ID"),
    decision: str | None = Query(default=None, description="Filter decisions by outcome (ALLOW, BLOCK, REQUIRE_APPROVAL)"),
    action: str | None = Query(default=None, description="Filter decisions by operation action"),
    resource: str | None = Query(default=None, description="Filter decisions by target resource"),
    decision_repo: DecisionRepository = Depends(get_decision_repository),
) -> DecisionSummary:
    """Calculates summary statistics, outcome counts, reason code distribution, and time bounds."""
    stats = decision_repo.get_decision_summary(
        trace_id=trace_id,
        policy_id=policy_id,
        decision=decision,
        action=action,
        resource=resource,
    )
    return DecisionSummary.model_validate(stats)


@router.post(
    "/decisions/purge",
    response_model=DecisionPurgeResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute retention purge on decision history records",
)
def purge_decisions(
    payload: DecisionPurgeRequest,
    decision_repo: DecisionRepository = Depends(get_decision_repository),
) -> DecisionPurgeResponse:
    """Purges historical decision audit records older than a configured timestamp or TTL.

    Enforces:
    - AI / LLM entities are forbidden from purging audit decision history (403 Forbidden).
    - Requires either before_timestamp or older_than_seconds (422 Unprocessable Entity).
    - Supports dry_run mode to preview eligible records without deleting.
    - Transactional database safety (500 Internal Server Error on persistence failure).
    """
    if payload.is_ai_or_llm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Automated AI/LLM entities are forbidden from purging audit decision history.",
        )

    target_cutoff = payload.before_timestamp
    if target_cutoff is None:
        if payload.older_than_seconds is not None:
            target_cutoff = datetime.now(timezone.utc) - timedelta(seconds=payload.older_than_seconds)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Either before_timestamp or older_than_seconds must be provided for retention purge.",
            )

    try:
        purged_count, remaining_count = decision_repo.purge_decisions(
            before_timestamp=target_cutoff,
            policy_id=payload.policy_id,
            dry_run=payload.dry_run,
        )
        return DecisionPurgeResponse(
            purged_count=purged_count,
            dry_run=payload.dry_run,
            remaining_count=remaining_count,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Decision retention purge failed safely: {exc}",
        ) from exc


@router.get(
    "/decisions/{decision_id}",
    response_model=DecisionRecord,
    status_code=status.HTTP_200_OK,
    summary="Retrieve details for a specific authorization decision by ID",
)
def get_decision(
    decision_id: str,
    decision_repo: DecisionRepository = Depends(get_decision_repository),
) -> DecisionRecord:
    """Retrieves an individual evaluated decision record from history."""
    rec = decision_repo.get_decision(decision_id)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decision '{decision_id}' not found.",
        )
    return DecisionRecord.model_validate(rec)

