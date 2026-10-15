"""FastAPI policy lifecycle management and CRUD API endpoints.

Exposes authenticated policy CRUD operations with application and environment
scope isolation, declarative safety validation, lifecycle transition management,
and retention enforcement following Member 3 Phase 4 specifications.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.api.authorization import (
    get_approval_service,
    get_policy_repository,
)
from backend.app.repositories.policy_repository import (
    PolicyPersistenceError,
    PolicyRepository,
)
from backend.app.schemas.authorization import AuthorizationRequest
from backend.app.schemas.policy import (
    ConflictDetectionResult,
    PolicyCreate,
    PolicyDiff,
    PolicyHistoryEntry,
    PolicyRead,
    PolicyRollbackRequest,
    PolicySimulationRequest,
    PolicySimulationResponse,
    PolicyUpdate,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.policy_service import PolicyService


class PolicyScope(BaseModel):
    """Application and environment governance scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)


class PolicyCallerIdentity(BaseModel):
    """Authenticated caller identity provided by security middleware."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    authorized_scopes: list[PolicyScope] = Field(min_length=1)
    is_ai_or_llm: bool = False


# Module-level default singleton dependency
_policy_service_instance: PolicyService | None = None


def get_policy_service(
    policy_repo: PolicyRepository = Depends(get_policy_repository),
    approval_service: ApprovalService = Depends(get_approval_service),
) -> PolicyService:
    """Dependency provider for PolicyService."""
    global _policy_service_instance
    if (
        _policy_service_instance is None
        or _policy_service_instance.policy_repo is not policy_repo
        or _policy_service_instance.approval_service is not approval_service
    ):
        _policy_service_instance = PolicyService(
            policy_repo=policy_repo,
            approval_service=approval_service,
        )
    return _policy_service_instance


def reset_policy_service(service: PolicyService | None = None) -> None:
    """Resets or overrides the default PolicyService singleton."""
    global _policy_service_instance
    _policy_service_instance = service


def get_authenticated_identity(request: Request) -> PolicyCallerIdentity:
    """Extracts and validates authenticated caller identity from request context.

    Enforces repository authentication convention: identity must be populated by
    trusted authentication middleware in request.state.
    """
    identity_raw = (
        getattr(request.state, "identity", None)
        or getattr(request.state, "operator_identity", None)
        or getattr(request.state, "auth_identity", None)
    )

    if identity_raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required for policy lifecycle operations.",
        )

    if isinstance(identity_raw, PolicyCallerIdentity):
        return identity_raw

    try:
        if isinstance(identity_raw, dict):
            # Auto-detect AI/LLM subjects if not explicitly set
            subject = identity_raw.get("subject", "")
            is_ai = identity_raw.get("is_ai_or_llm", False)
            if not is_ai and subject.lower().startswith(("ai:", "llm:", "bot:", "agent:")):
                identity_raw = dict(identity_raw)
                identity_raw["is_ai_or_llm"] = True
            return PolicyCallerIdentity.model_validate(identity_raw)

        # Object attribute mapping (e.g. OperatorIdentity compatibility)
        subject = getattr(identity_raw, "subject", "")
        raw_scopes = getattr(identity_raw, "authorized_scopes", [])
        scopes = [
            PolicyScope(
                application_id=s.application_id if hasattr(s, "application_id") else s["application_id"],
                environment=s.environment if hasattr(s, "environment") else s["environment"],
            )
            for s in raw_scopes
        ]
        is_ai = getattr(identity_raw, "is_ai_or_llm", False)
        if not is_ai and subject.lower().startswith(("ai:", "llm:", "bot:", "agent:")):
            is_ai = True
        return PolicyCallerIdentity(
            subject=subject,
            authorized_scopes=scopes,
            is_ai_or_llm=is_ai,
        )
    except (ValidationError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"The authenticated identity is invalid: {exc}",
        ) from exc


def ensure_human_authority(identity: PolicyCallerIdentity) -> None:
    """Strictly prevents AI/LLM entities from mutating governance policies."""
    if identity.is_ai_or_llm or identity.subject.lower().startswith(
        ("ai:", "llm:", "bot:", "agent:")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Automated AI/LLM entities are forbidden from creating, modifying, or deleting governance policies. Explicit human authorization is required.",
        )


def resolve_requested_scope(
    identity: PolicyCallerIdentity,
    application_id: str | None = None,
    environment: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolves and authorizes the requested application and environment scope against caller claims."""
    if application_id is not None or environment is not None:
        # Validate that the requested scope is authorized
        is_authorized = any(
            (application_id is None or s.application_id in (application_id, "*"))
            and (environment is None or s.environment in (environment, "*"))
            for s in identity.authorized_scopes
        )
        if not is_authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The requested application or environment scope is not authorized for this identity.",
            )
        return application_id, environment

    # When scope parameters are omitted:
    if len(identity.authorized_scopes) == 1:
        single_scope = identity.authorized_scopes[0]
        return single_scope.application_id, single_scope.environment

    return None, None


router = APIRouter(tags=["policies"])


@router.get(
    "",
    response_model=list[PolicyRead],
    status_code=status.HTTP_200_OK,
    summary="List governance policies within caller's authorized scope",
)
def list_policies(
    application_id: str | None = Query(
        default=None,
        description="Optional filter by application identifier",
    ),
    environment: str | None = Query(
        default=None,
        description="Optional filter by execution environment (e.g. 'production', 'staging')",
    ),
    enabled_only: bool | None = Query(
        default=None,
        description="Filter to active enabled policies only",
    ),
    action: str | None = Query(
        default=None,
        description="Filter to policies applicable to target action",
    ),
    resource: str | None = Query(
        default=None,
        description="Filter to policies applicable to target resource",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of policies to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of policies to skip for pagination",
    ),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> list[PolicyRead]:
    """Lists policies within the caller's authorized application and environment scope.

    Enforces scope isolation: policies restricted to other applications or environments
    are excluded from the response.
    """
    app_id, env = resolve_requested_scope(identity, application_id, environment)
    return service.list_policies(
        application_id=app_id,
        environment=env,
        authorized_scopes=identity.authorized_scopes,
        enabled_only=enabled_only,
        action=action,
        resource=resource,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{policy_id}",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an individual policy by its stable identifier",
)
def get_policy(
    policy_id: str,
    version: int | None = Query(
        default=None,
        ge=1,
        description="Optional historical revision version number",
    ),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Retrieves an individual policy definition by its stable identifier.

    Enforces scope isolation: returns 404 or 403 if the policy targets a scope
    outside the caller's authorized scopes.
    """
    try:
        policy = service.get_policy(
            policy_id=policy_id,
            version=version,
            authorized_scopes=identity.authorized_scopes,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if policy is None:
        if version is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Policy '{policy_id}' revision {version} not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        )
    return policy


@router.get(
    "/{policy_id}/revisions",
    response_model=list[PolicyRead],
    status_code=status.HTTP_200_OK,
    summary="List all historical revisions for a policy in ascending version order",
)
def list_policy_revisions(
    policy_id: str,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> list[PolicyRead]:
    """Retrieves all historical revisions of a policy in ascending version order.

    Enforces scope isolation: returns 404 or 403 if the policy targets a scope
    outside the caller's authorized scopes.
    """
    try:
        revisions = service.get_policy_revisions(
            policy_id=policy_id,
            authorized_scopes=identity.authorized_scopes,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if not revisions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        )
    return revisions


@router.get(
    "/{policy_id}/history",
    response_model=list[PolicyHistoryEntry],
    status_code=status.HTTP_200_OK,
    summary="List audit change history entries for a policy in chronological order",
)
def list_policy_history(
    policy_id: str,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> list[PolicyHistoryEntry]:
    """Retrieves audit change history entries for a policy in chronological order.

    Enforces scope isolation: returns 404 or 403 if the policy targets a scope
    outside the caller's authorized scopes.
    """
    try:
        history = service.get_change_history(
            policy_id=policy_id,
            authorized_scopes=identity.authorized_scopes,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if not history:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        )
    return history


@router.get(
    "/{policy_id}/diff",
    response_model=PolicyDiff,
    status_code=status.HTTP_200_OK,
    summary="Compare two historical policy revisions",
)
def compare_policy_revisions(
    policy_id: str,
    from_version: int = Query(..., ge=1, description="Base revision version number"),
    to_version: int | None = Query(default=None, ge=1, description="Target revision version number (defaults to current)"),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyDiff:
    """Computes semantic and structural differences between two policy revisions.

    Enforces scope isolation: returns 404 or 403 if the policy targets a scope
    outside caller's authorization.
    """
    try:
        return service.compare_policy_versions(
            policy_id=policy_id,
            from_version=from_version,
            to_version=to_version,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.post(
    "/{policy_id}/diff",
    response_model=PolicyDiff,
    status_code=status.HTTP_200_OK,
    summary="Preview diff of a proposed policy update without applying it",
)
def preview_policy_diff(
    policy_id: str,
    update: PolicyUpdate,
    base_version: int | None = Query(default=None, ge=1, description="Base revision version number to diff against (defaults to current)"),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyDiff:
    """Previews differences that would be introduced by a proposed update without mutating state.

    Enforces declarative safety (422) and scope authorization (403).
    """
    try:
        return service.preview_policy_update(
            policy_id=policy_id,
            update=update,
            base_version=base_version,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.post(
    "/simulate",
    response_model=PolicySimulationResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate policy evaluation against a candidate or specified policy without side effects",
)
def simulate_policy_dry_run(
    payload: PolicySimulationRequest,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicySimulationResponse:
    """Evaluates a hypothetical AuthorizationRequest against a candidate policy or specified policy.

    Guarantees:
    - Purely analytical and read-only: does not persist decisions to DecisionRepository
      and does not generate pending human ApprovalRequests.
    - Full execution trace: reports per-rule condition matches, scope filters, and reasons.
    - Rule conflict detection: reports shadowed, redundant, opposing, or disabled rules.
    - Scope isolation: returns 403 Forbidden if candidate/target policy targets unauthorized scope.
    - Safety validation: returns 422 Unprocessable Entity if candidate policy contains code/SQL injection.
    """
    try:
        resolved_policy_id = (
            payload.candidate_policy.id if payload.candidate_policy else payload.policy_id
        )
        return service.simulate_policy(
            request=payload.request,
            policy_id=resolved_policy_id,
            candidate_policy=payload.candidate_policy,
            version=payload.version,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.post(
    "/conflicts",
    response_model=list[ConflictDetectionResult],
    status_code=status.HTTP_200_OK,
    summary="Analyze rule conflicts, shadows, and disabled rules for a candidate policy definition",
)
def analyze_candidate_conflicts(
    payload: PolicyCreate,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> list[ConflictDetectionResult]:
    """Analyzes a candidate policy definition for internal conflicts, shadowing, opposing effects, and disabled rules.

    Enforces:
    - Scope authorization (403 Forbidden if candidate targets unauthorized scope).
    - Declarative condition safety (422 Unprocessable Entity if containing code/SQL injection).
    """
    try:
        return service.detect_conflicts(
            candidate_policy=payload,
            authorized_scopes=identity.authorized_scopes,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "/{policy_id}/simulate",
    response_model=PolicySimulationResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate evaluation of an existing policy against a hypothetical request",
)
def simulate_existing_policy(
    policy_id: str,
    payload: PolicySimulationRequest,
    version: int | None = Query(default=None, ge=1, description="Specific historical revision version to simulate against"),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicySimulationResponse:
    """Simulates an existing policy (or specific historical revision) against a hypothetical request.

    Guarantees:
    - Zero side effects: no writes to decision audit logs or approval queues.
    - Scope authorization enforced (403 Forbidden if caller lacks scope claims).
    - 404 Not Found if policy or specified revision does not exist.
    """
    try:
        resolved_version = payload.version if payload.version is not None else version
        return service.simulate_policy(
            request=payload.request,
            policy_id=policy_id,
            candidate_policy=payload.candidate_policy,
            version=resolved_version,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.get(
    "/{policy_id}/conflicts",
    response_model=list[ConflictDetectionResult],
    status_code=status.HTTP_200_OK,
    summary="Analyze rule conflicts, shadows, and disabled rules for a policy",
)
def analyze_policy_conflicts(
    policy_id: str,
    version: int | None = Query(default=None, ge=1, description="Optional revision version number to inspect"),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> list[ConflictDetectionResult]:
    """Inspects a policy for rule shadowing, redundancies, and conflicting effects under configured resolution algorithm."""
    try:
        return service.detect_conflicts(
            policy_id=policy_id,
            version=version,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.post(
    "",
    response_model=PolicyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new governance policy",
)
def create_policy(
    policy: PolicyCreate,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Creates initial revision (v1) of a new security governance policy.

    Enforces:
    - AI / LLM entities are forbidden from creating policies (403 Forbidden).
    - Scope authorization: policy rules must not target scopes outside caller's authorization (403 Forbidden).
    - Declarative condition safety: rejects arbitrary code execution and SQL fragments (422 Unprocessable Entity).
    - ID uniqueness: duplicate policy IDs are rejected (409 Conflict).
    - Transactional persistence failure handling (500 Internal Server Error).
    """
    ensure_human_authority(identity)

    try:
        return service.create_policy(
            policy=policy,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
        )
    except ValueError as exc:
        msg = str(exc)
        if "already exists" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=msg,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc


@router.put(
    "/{policy_id}",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Update policy metadata and supported conditions",
)
@router.patch(
    "/{policy_id}",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Update policy metadata and supported conditions",
)
def update_policy(
    policy_id: str,
    update: PolicyUpdate,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Updates an existing policy, creating an immutable new revision.

    Enforces:
    - AI / LLM entities are forbidden from modifying policies (403 Forbidden).
    - Scope authorization: caller must be authorized for policy's scope (403 Forbidden).
    - Declarative safety: rejects arbitrary expressions, Python code, and SQL fragments (422 Unprocessable Entity).
    - Deactivation handling: invalidates pending approvals if policy is disabled.
    - Transactional persistence failure handling (500 Internal Server Error).
    """
    ensure_human_authority(identity)

    try:
        return service.update_policy(
            policy_id=policy_id,
            update=update,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc


@router.post(
    "/{policy_id}/enable",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Enable a policy through supported lifecycle update",
)
def enable_policy(
    policy_id: str,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Enables a policy through the existing supported update mechanism."""
    ensure_human_authority(identity)

    try:
        return service.enable_policy(
            policy_id=policy_id,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc


@router.post(
    "/{policy_id}/disable",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Disable a policy through supported lifecycle update",
)
def disable_policy(
    policy_id: str,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Disables a policy through the existing supported update mechanism, expiring pending approvals."""
    ensure_human_authority(identity)

    try:
        return service.disable_policy(
            policy_id=policy_id,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc


@router.delete(
    "/{policy_id}",
    status_code=status.HTTP_200_OK,
    summary="Retire or delete a policy under existing retention rules",
)
def delete_policy(
    policy_id: str,
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> dict[str, Any]:
    """Retires and deletes a policy under existing retention rules.

    Enforces:
    - Preserves historical revisions in immutable history.
    - Invalidates all pending approval requests linked to this policy.
    - Subsequent runtime evaluation fails closed.
    - Transactional persistence failure handling (500 Internal Server Error).
    """
    ensure_human_authority(identity)

    try:
        service.delete_policy(
            policy_id=policy_id,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
        )
        return {
            "id": policy_id,
            "status": "deleted",
            "message": f"Policy '{policy_id}' retired and deleted from active definitions.",
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc


@router.post(
    "/{policy_id}/rollback",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Rollback a policy to a historical revision, creating a new monotonic version",
)
@router.post(
    "/{policy_id}/revert",
    response_model=PolicyRead,
    status_code=status.HTTP_200_OK,
    summary="Revert a policy to a historical revision, creating a new monotonic version",
)
def rollback_policy(
    policy_id: str,
    payload: PolicyRollbackRequest | None = None,
    target_version: int | None = Query(
        default=None,
        ge=1,
        description="Target historical revision version number to restore",
    ),
    identity: PolicyCallerIdentity = Depends(get_authenticated_identity),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyRead:
    """Restores a policy definition from a historical revision as a new monotonic version.

    Enforces:
    - AI / LLM entities are strictly forbidden from executing rollbacks (403 Forbidden).
    - Caller must have authorization for policy application/environment scope (403 Forbidden).
    - Target revision must exist in immutable history (404 Not Found).
    - Cannot rollback to the already-active revision (422 Unprocessable Entity).
    - Automatically invalidates all pending approval requests tied to prior revisions (drift protection).
    - Transactional persistence failure handling (500 Internal Server Error).
    """
    ensure_human_authority(identity)

    resolved_version = payload.target_version if payload is not None else target_version
    if resolved_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="target_version must be provided in request body or query parameter.",
        )

    resolved_description = payload.description if payload is not None else None

    try:
        return service.rollback_policy(
            policy_id=policy_id,
            target_version=resolved_version,
            actor=identity.subject,
            authorized_scopes=identity.authorized_scopes,
            description=resolved_description,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'\""),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PolicyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failure: {exc}",
        ) from exc