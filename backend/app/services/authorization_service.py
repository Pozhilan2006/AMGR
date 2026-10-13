"""Authorization service orchestrating policy evaluation, decision history, and approval workflows.

Integrates PolicyRepository, DecisionRepository, deterministic policy engine evaluation,
and ApprovalService into the runtime authorization decision boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.policy_engine.evaluator import evaluate_policies
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import ApprovalRequest, ApprovalStatus
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.policy_enums import PolicyEffect, RuleResolutionAlgorithm
from backend.app.services.approval_service import ApprovalService


class AuthorizationService:
    """Coordinates deterministic runtime authorization decisions and approval workflows.

    Enforces:
    - Pre-check for existing granted approvals (verifying human authority, single-decision finality,
      and lack of policy version drift).
    - Deterministic evaluation of active policies from PolicyRepository.
    - Automatic creation of pending ApprovalRequest on REQUIRE_APPROVAL outcomes.
    - Fail-closed deterministic fallback when no policies match or policies are disabled.
    - Immutable decision audit history recording into DecisionRepository.
    """

    def __init__(
        self,
        policy_repo: PolicyRepository | None = None,
        approval_service: ApprovalService | None = None,
        decision_repo: DecisionRepository | None = None,
    ) -> None:
        self.policy_repo = policy_repo if policy_repo is not None else PolicyRepository()
        self.approval_service = (
            approval_service if approval_service is not None else ApprovalService()
        )
        self.decision_repo = (
            decision_repo if decision_repo is not None else DecisionRepository()
        )

    def _complete(
        self,
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
        appr_req: ApprovalRequest | None = None,
    ) -> tuple[AuthorizationDecision, ApprovalRequest | None]:
        """Records decision into DecisionRepository and returns outcome."""
        self.decision_repo.record_decision(request, decision)
        return decision, appr_req

    def authorize(
        self, request: AuthorizationRequest
    ) -> tuple[AuthorizationDecision, ApprovalRequest | None]:
        """Evaluates an inbound authorization request against active governance policies.

        Returns:
            A tuple of (AuthorizationDecision, ApprovalRequest | None).
            ApprovalRequest is non-None only when REQUIRE_APPROVAL is newly triggered.
        """
        now = datetime.now(timezone.utc)

        # 1. Check if the request context carries an existing approval_id
        approval_id = (
            request.context.get("approval_id")
            if isinstance(request.context, dict)
            else None
        )
        if approval_id is not None:
            appr = self.approval_service.get_approval_request(str(approval_id))
            if appr is None:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id=None,
                    matched_rule_id=None,
                    matched_rule_ids=[],
                    matched_policy_ids=[],
                    reason_code=ReasonCode.INVALID_REQUEST,
                    reason_message=f"Approval request '{approval_id}' not found.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            can_proceed = self.approval_service.can_proceed(
                str(approval_id), policy_repo=self.policy_repo
            )
            if can_proceed:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.ALLOW,
                    policy_id=appr.policy_id,
                    matched_rule_id=appr.matched_rule_id,
                    matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                    matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Human approval '{approval_id}' verified. Granted by '{appr.decided_by}'.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            # Cannot proceed: determine precise reason
            if appr.status == ApprovalStatus.PENDING:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.REQUIRE_APPROVAL,
                    policy_id=appr.policy_id,
                    matched_rule_id=appr.matched_rule_id,
                    matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                    matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Approval request '{approval_id}' is pending human review.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            if appr.status == ApprovalStatus.REJECTED:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id=appr.policy_id,
                    matched_rule_id=appr.matched_rule_id,
                    matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                    matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Approval request '{approval_id}' was rejected by '{appr.decided_by}'.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            if appr.status == ApprovalStatus.EXPIRED:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id=appr.policy_id,
                    matched_rule_id=appr.matched_rule_id,
                    matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                    matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Approval request '{approval_id}' has expired due to policy revision drift.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            # Policy disabled or removed after approval
            current_pol = self.policy_repo.get_policy(appr.policy_id)
            if current_pol is None or not current_pol.enabled:
                decision = AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id=appr.policy_id,
                    matched_rule_id=appr.matched_rule_id,
                    matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                    matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                    reason_code=ReasonCode.POLICY_DISABLED,
                    reason_message=f"Policy '{appr.policy_id}' is disabled or removed; execution blocked.",
                    evaluated_at=now,
                )
                return self._complete(request, decision, None)

            # Fallback fail-closed
            decision = AuthorizationDecision(
                decision=PolicyEffect.BLOCK,
                policy_id=appr.policy_id,
                matched_rule_id=appr.matched_rule_id,
                matched_rule_ids=[appr.matched_rule_id] if appr.matched_rule_id else [],
                matched_policy_ids=[appr.policy_id] if appr.policy_id else [],
                reason_code=ReasonCode.DEFAULT_FALLBACK,
                reason_message=f"Execution blocked for approval request '{approval_id}'.",
                evaluated_at=now,
            )
            return self._complete(request, decision, None)

        # 2. Standard deterministic policy evaluation
        active_policies = self.policy_repo.list_all_policies()
        raw_decision = evaluate_policies(
            policies=active_policies,
            request=request,
            default_effect=PolicyEffect.BLOCK,
            conflict_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
        )

        # 3. If REQUIRE_APPROVAL outcome, automatically initiate approval workflow
        if raw_decision.decision == PolicyEffect.REQUIRE_APPROVAL:
            policy_version = 1
            if raw_decision.policy_id:
                p = self.policy_repo.get_policy(raw_decision.policy_id)
                if p is not None:
                    policy_version = p.version

            appr_req = self.approval_service.create_approval_request(
                authorization_decision=raw_decision,
                authorization_request=request,
                policy_version=policy_version,
            )

            # Enrich decision message with the created approval ID
            enriched_decision = AuthorizationDecision(
                decision=raw_decision.decision,
                policy_id=raw_decision.policy_id,
                matched_rule_id=raw_decision.matched_rule_id,
                matched_rule_ids=raw_decision.matched_rule_ids,
                matched_policy_ids=raw_decision.matched_policy_ids,
                reason_code=raw_decision.reason_code,
                reason_message=f"{raw_decision.reason_message} Created human approval request '{appr_req.id}'.",
                evaluated_at=raw_decision.evaluated_at,
            )
            return self._complete(request, enriched_decision, appr_req)

        return self._complete(request, raw_decision, None)
