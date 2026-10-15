"""Human approval workflow service and authorization safety manager for AMGR.

Manages pending human approval requests, verifies human authority,
enforces single-decision finality, guards against policy version drift,
and strictly prevents automated AI/LLM overrides following the Member 3 roadmap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import (
    ApprovalDecisionSubmission,
    ApprovalDecisionType,
    ApprovalRequest,
    ApprovalStatus,
)
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from backend.app.schemas.policy_enums import PolicyEffect


class ApprovalService:
    """In-memory service managing human approval requests and authorization safety.

    Guarantees:
    - Only REQUIRE_APPROVAL decisions can initiate an approval workflow.
    - Pending approvals are never treated as grants (fail-closed until explicit human approval).
    - Approvals must be explicit, authenticated actions by recognized human actors.
    - Automated AI operators and LLMs are strictly forbidden from approving requests.
    - An already finalized approval cannot be overwritten or contradicted.
    - Stale approvals are invalidated if the underlying policy revision changes.
    - Time-based expiration and TTL execution validity window enforcement.
    """

    def __init__(
        self,
        allowed_approvers: Sequence[str] | set[str] | None = None,
        default_ttl_seconds: int | None = None,
    ) -> None:
        # Maps approval_id -> ApprovalRequest
        self._requests: dict[str, ApprovalRequest] = {}
        # Optional whitelist of authorized human reviewer identities
        self._allowed_approvers: set[str] | None = (
            set(allowed_approvers) if allowed_approvers is not None else None
        )
        self._default_ttl_seconds = default_ttl_seconds
        self._counter: int = 0

    def create_approval_request(
        self,
        authorization_decision: AuthorizationDecision,
        authorization_request: AuthorizationRequest,
        policy_version: int = 1,
        ttl_seconds: int | None = None,
        expires_at: datetime | None = None,
    ) -> ApprovalRequest:
        """Creates a pending human approval request from a REQUIRE_APPROVAL outcome.

        Raises:
            ValueError: If the authorization decision is not REQUIRE_APPROVAL, or lacks policy_id.
        """
        if authorization_decision.decision != PolicyEffect.REQUIRE_APPROVAL:
            raise ValueError(
                f"Cannot create human approval request for authorization outcome '{authorization_decision.decision.value}'. "
                "Only REQUIRE_APPROVAL outcomes initiate human workflows."
            )

        if not authorization_decision.policy_id:
            raise ValueError(
                "Authorization decision must include a valid policy_id to establish an approval request."
            )

        self._counter += 1
        approval_id = f"appr_{self._counter:04d}"

        now = datetime.now(timezone.utc)
        resolved_expires_at = expires_at
        if resolved_expires_at is None:
            effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
            if effective_ttl is not None and effective_ttl > 0:
                resolved_expires_at = now + timedelta(seconds=effective_ttl)

        req = ApprovalRequest(
            id=approval_id,
            trace_id=authorization_request.trace_id,
            policy_id=authorization_decision.policy_id,
            policy_version=policy_version,
            matched_rule_id=authorization_decision.matched_rule_id,
            original_decision=authorization_decision,
            request_payload=authorization_request,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=resolved_expires_at,
            decided_at=None,
            decided_by=None,
            decision_reason=None,
            final_effect=PolicyEffect.REQUIRE_APPROVAL,
        )

        self._requests[approval_id] = req
        return req

    def submit_decision(
        self,
        approval_id: str,
        submission: ApprovalDecisionSubmission,
        policy_repo: PolicyRepository | None = None,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Records an explicit human approval or rejection.

        Raises:
            KeyError: If the approval request does not exist.
            ValueError: If the request is already finalized, expired, or if policy revision drift is detected.
            PermissionError: If actor is unauthorized or represents an automated AI/LLM entity.
        """
        if approval_id not in self._requests:
            raise KeyError(f"Approval request '{approval_id}' not found.")

        req = self._requests[approval_id]
        current_time = now if now is not None else datetime.now(timezone.utc)

        # 0. Check time-based expiration
        if req.is_expired(current_time):
            if req.status != ApprovalStatus.EXPIRED:
                expired_req = ApprovalRequest(
                    id=req.id,
                    trace_id=req.trace_id,
                    policy_id=req.policy_id,
                    policy_version=req.policy_version,
                    matched_rule_id=req.matched_rule_id,
                    original_decision=req.original_decision,
                    request_payload=req.request_payload,
                    status=ApprovalStatus.EXPIRED,
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                    decided_at=current_time,
                    decided_by=submission.actor,
                    decision_reason=(
                        f"Approval request expired at {req.expires_at.isoformat()} before decision was submitted."
                        if req.expires_at
                        else "Approval request has expired."
                    ),
                    final_effect=PolicyEffect.BLOCK,
                )
                self._requests[approval_id] = expired_req
            raise ValueError(
                f"Approval request '{approval_id}' has expired and can no longer be decided."
            )

        # 1. Enforce single-decision finality
        if req.is_terminal():
            raise ValueError(
                f"Approval request '{approval_id}' is already finalized with status '{req.status.value}'. "
                "Duplicate or contradictory decisions are strictly forbidden."
            )

        # 2. Prevent AI / LLM overrides
        if submission.is_ai_or_llm:
            raise PermissionError(
                "Automated AI/LLM entities are forbidden from resolving governance approvals."
            )
        actor_lower = submission.actor.lower()
        if actor_lower.startswith("ai:") or actor_lower.startswith("llm:"):
            raise PermissionError(
                f"Actor '{submission.actor}' is an automated entity. Only authorized human reviewers may resolve requests."
            )

        # 3. Check authorized human reviewers whitelist (if configured)
        if self._allowed_approvers is not None and submission.actor not in self._allowed_approvers:
            raise PermissionError(
                f"Actor '{submission.actor}' is not in the authorized approvers registry."
            )

        # 4. Check policy revision drift: policy must not have mutated since request creation
        if policy_repo is not None:
            current_policy = policy_repo.get_policy(req.policy_id)
            if current_policy is None:
                expired_req = ApprovalRequest(
                    id=req.id,
                    trace_id=req.trace_id,
                    policy_id=req.policy_id,
                    policy_version=req.policy_version,
                    matched_rule_id=req.matched_rule_id,
                    original_decision=req.original_decision,
                    request_payload=req.request_payload,
                    status=ApprovalStatus.EXPIRED,
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                    decided_at=current_time,
                    decided_by=submission.actor,
                    decision_reason=f"Policy '{req.policy_id}' was deleted; approval invalidated.",
                    final_effect=PolicyEffect.BLOCK,
                )
                self._requests[approval_id] = expired_req
                raise ValueError(
                    f"Policy '{req.policy_id}' no longer exists; approval request has been expired and blocked."
                )

            if current_policy.version != req.policy_version:
                expired_req = ApprovalRequest(
                    id=req.id,
                    trace_id=req.trace_id,
                    policy_id=req.policy_id,
                    policy_version=req.policy_version,
                    matched_rule_id=req.matched_rule_id,
                    original_decision=req.original_decision,
                    request_payload=req.request_payload,
                    status=ApprovalStatus.EXPIRED,
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                    decided_at=current_time,
                    decided_by=submission.actor,
                    decision_reason=(
                        f"Policy '{req.policy_id}' was updated from revision {req.policy_version} "
                        f"to revision {current_policy.version} before approval decision; invalidated."
                    ),
                    final_effect=PolicyEffect.BLOCK,
                )
                self._requests[approval_id] = expired_req
                raise ValueError(
                    f"Policy '{req.policy_id}' revision changed (v{req.policy_version} -> v{current_policy.version}) "
                    "before approval decision. Approval request has been invalidated."
                )

        # 5. Apply explicit decision
        if submission.decision == ApprovalDecisionType.APPROVE:
            new_status = ApprovalStatus.APPROVED
            new_effect = PolicyEffect.ALLOW
        else:
            new_status = ApprovalStatus.REJECTED
            new_effect = PolicyEffect.BLOCK

        final_req = ApprovalRequest(
            id=req.id,
            trace_id=req.trace_id,
            policy_id=req.policy_id,
            policy_version=req.policy_version,
            matched_rule_id=req.matched_rule_id,
            original_decision=req.original_decision,
            request_payload=req.request_payload,
            status=new_status,
            created_at=req.created_at,
            expires_at=req.expires_at,
            decided_at=current_time,
            decided_by=submission.actor,
            decision_reason=submission.reason,
            final_effect=new_effect,
        )

        self._requests[approval_id] = final_req
        return final_req

    def cancel_approval(
        self,
        approval_id: str,
        actor: str,
        reason: str,
        is_ai_or_llm: bool = False,
    ) -> ApprovalRequest:
        """Explicitly cancels/invalidates a pending or active approval request.

        Raises:
            KeyError: If the approval request does not exist.
            ValueError: If the request is already in a terminal state (REJECTED or EXPIRED).
            PermissionError: If actor represents an automated AI/LLM entity.
        """
        if approval_id not in self._requests:
            raise KeyError(f"Approval request '{approval_id}' not found.")

        req = self._requests[approval_id]
        if req.status in (ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED):
            raise ValueError(
                f"Approval request '{approval_id}' is already finalized with status '{req.status.value}'."
            )

        if is_ai_or_llm or actor.lower().startswith(("ai:", "llm:", "bot:", "agent:")):
            raise PermissionError(
                "Automated AI/LLM entities are forbidden from cancelling governance approval requests."
            )

        now = datetime.now(timezone.utc)
        cancelled = ApprovalRequest(
            id=req.id,
            trace_id=req.trace_id,
            policy_id=req.policy_id,
            policy_version=req.policy_version,
            matched_rule_id=req.matched_rule_id,
            original_decision=req.original_decision,
            request_payload=req.request_payload,
            status=ApprovalStatus.EXPIRED,
            created_at=req.created_at,
            expires_at=req.expires_at,
            decided_at=now,
            decided_by=actor,
            decision_reason=reason or f"Cancelled by {actor}.",
            final_effect=PolicyEffect.BLOCK,
        )
        self._requests[approval_id] = cancelled
        return cancelled

    def expire_stale_requests(self, now: datetime | None = None) -> list[str]:
        """Scans all approval requests and transitions stale pending/approved requests to EXPIRED."""
        current_time = now if now is not None else datetime.now(timezone.utc)
        expired_ids: list[str] = []
        for approval_id, req in list(self._requests.items()):
            if req.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
                if req.expires_at is not None and current_time >= req.expires_at:
                    self._requests[approval_id] = ApprovalRequest(
                        id=req.id,
                        trace_id=req.trace_id,
                        policy_id=req.policy_id,
                        policy_version=req.policy_version,
                        matched_rule_id=req.matched_rule_id,
                        original_decision=req.original_decision,
                        request_payload=req.request_payload,
                        status=ApprovalStatus.EXPIRED,
                        created_at=req.created_at,
                        expires_at=req.expires_at,
                        decided_at=current_time,
                        decided_by="system",
                        decision_reason="Request exceeded configured TTL without resolution.",
                        final_effect=PolicyEffect.BLOCK,
                    )
                    expired_ids.append(approval_id)
        return expired_ids

    def can_proceed(
        self,
        approval_id: str,
        policy_repo: PolicyRepository | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Determines if the protected operation is authorized to proceed.

        Fails closed (returns False) for:
        - Missing or invalid approval_id
        - Pending approval status
        - Rejected or expired approval status
        - Expired execution validity window (TTL)
        - Any policy revision mismatch or deactivated policy
        """
        if approval_id not in self._requests:
            return False

        req = self._requests[approval_id]
        current_time = now if now is not None else datetime.now(timezone.utc)

        # Check time-based expiration
        if req.is_expired(current_time):
            if req.status != ApprovalStatus.EXPIRED:
                expired_req = ApprovalRequest(
                    id=req.id,
                    trace_id=req.trace_id,
                    policy_id=req.policy_id,
                    policy_version=req.policy_version,
                    matched_rule_id=req.matched_rule_id,
                    original_decision=req.original_decision,
                    request_payload=req.request_payload,
                    status=ApprovalStatus.EXPIRED,
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                    decided_at=current_time,
                    decided_by="system",
                    decision_reason="Approval request execution window expired.",
                    final_effect=PolicyEffect.BLOCK,
                )
                self._requests[approval_id] = expired_req
            return False

        if not req.is_approved(current_time):
            return False

        if req.final_effect != PolicyEffect.ALLOW:
            return False

        # Additional verification against current policy store
        if policy_repo is not None:
            current_policy = policy_repo.get_policy(req.policy_id)
            if current_policy is None:
                return False
            if not current_policy.enabled:
                return False
            if current_policy.version != req.policy_version:
                return False

        return True

    def get_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        """Retrieves an approval request by identifier, checking time-based expiration."""
        req = self._requests.get(approval_id)
        if req is not None and req.is_expired():
            if req.status != ApprovalStatus.EXPIRED:
                self.expire_stale_requests()
                return self._requests.get(approval_id)
        return req

    def list_pending_requests(self) -> list[ApprovalRequest]:
        """Returns all approval requests currently in PENDING status."""
        self.expire_stale_requests()
        return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]

    def list_all_requests(self) -> list[ApprovalRequest]:
        """Returns all approval requests."""
        self.expire_stale_requests()
        return list(self._requests.values())

    def list_approvals(
        self,
        status_filter: ApprovalStatus | None = None,
        trace_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """Lists approval requests with multi-parameter filtering and deterministic pagination."""
        self.expire_stale_requests()
        results = list(self._requests.values())
        if status_filter is not None:
            results = [r for r in results if r.status == status_filter]
        if trace_id is not None:
            results = [r for r in results if r.trace_id == trace_id]
        if policy_id is not None:
            results = [r for r in results if r.policy_id == policy_id]

        results.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return results[offset : offset + limit]

    def invalidate_approvals_for_policy(
        self, policy_id: str, reason: str = "Policy deactivated or deleted."
    ) -> list[str]:
        """Invalidates all pending approval requests linked to a specified policy."""
        now = datetime.now(timezone.utc)
        invalidated: list[str] = []
        for req_id, req in list(self._requests.items()):
            if req.policy_id == policy_id and req.status == ApprovalStatus.PENDING:
                expired_req = ApprovalRequest(
                    id=req.id,
                    trace_id=req.trace_id,
                    policy_id=req.policy_id,
                    policy_version=req.policy_version,
                    matched_rule_id=req.matched_rule_id,
                    original_decision=req.original_decision,
                    request_payload=req.request_payload,
                    status=ApprovalStatus.EXPIRED,
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                    decided_at=now,
                    decided_by="system",
                    decision_reason=reason,
                    final_effect=PolicyEffect.BLOCK,
                )
                self._requests[req_id] = expired_req
                invalidated.append(req_id)
        return invalidated

