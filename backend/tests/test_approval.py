"""Unit tests for AMGR human approval workflow and authorization safety.

Validates creation of pending approval requests, explicit human approval/rejection,
prevention of AI/LLM approvals, unauthorized reviewer rejection, single-decision finality,
policy revision drift invalidation, and end-to-end integration with the policy engine.
"""

import unittest
from datetime import datetime, timezone

from backend.app.policy_engine.evaluator import evaluate_policy
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
    ReasonCode,
)
from backend.app.schemas.condition import (
    PYDANTIC_AVAILABLE,
    ConditionRule,
)
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
)
from backend.app.services.approval_service import ApprovalService

try:
    from pydantic import ValidationError
except ModuleNotFoundError:
    ValidationError = Exception  # type: ignore[assignment, misc]


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestApprovalRequestCreation(unittest.TestCase):
    """Verifies that approval requests are created properly from REQUIRE_APPROVAL outcomes."""

    def setUp(self) -> None:
        self.service = ApprovalService()
        self.auth_req = AuthorizationRequest(
            trace_id="trc_appr_01",
            subject="operator_bob",
            action="deploy_db",
            resource="prod_postgres",
        )
        self.auth_decision_req_appr = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_db_deploy",
            matched_rule_id="rule_require_lead",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Deployment requires lead review.",
        )

    def test_create_valid_pending_approval_request(self) -> None:
        """REQUIRE_APPROVAL creates an identifiable PENDING approval request."""
        appr_req = self.service.create_approval_request(
            self.auth_decision_req_appr,
            self.auth_req,
            policy_version=1,
        )

        self.assertTrue(appr_req.id.startswith("appr_"))
        self.assertEqual(appr_req.trace_id, "trc_appr_01")
        self.assertEqual(appr_req.policy_id, "pol_db_deploy")
        self.assertEqual(appr_req.policy_version, 1)
        self.assertEqual(appr_req.status, ApprovalStatus.PENDING)
        self.assertEqual(appr_req.final_effect, PolicyEffect.REQUIRE_APPROVAL)
        self.assertTrue(appr_req.is_pending())
        self.assertFalse(appr_req.is_approved())
        self.assertFalse(appr_req.is_terminal())
        self.assertIsNone(appr_req.decided_at)
        self.assertIsNone(appr_req.decided_by)

    def test_reject_creation_for_allow_or_block_outcomes(self) -> None:
        """Only REQUIRE_APPROVAL outcomes can initiate an approval workflow."""
        decision_allow = AuthorizationDecision(
            decision=PolicyEffect.ALLOW,
            policy_id="pol_ok",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Allowed.",
        )
        decision_block = AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            policy_id="pol_bad",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Blocked.",
        )

        with self.assertRaises(ValueError):
            self.service.create_approval_request(decision_allow, self.auth_req)

        with self.assertRaises(ValueError):
            self.service.create_approval_request(decision_block, self.auth_req)

    def test_reject_creation_without_policy_id(self) -> None:
        """Decision lacking policy_id must not create an approval request."""
        decision_no_pol = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id=None,
            reason_code=ReasonCode.DEFAULT_FALLBACK,
            reason_message="No policy specified.",
        )
        with self.assertRaises(ValueError):
            self.service.create_approval_request(decision_no_pol, self.auth_req)


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestApprovalDecisionEnforcement(unittest.TestCase):
    """Verifies explicit approval/rejection behavior and permission gating."""

    def setUp(self) -> None:
        self.service = ApprovalService(allowed_approvers=["alice_secops", "carol_lead"])
        self.auth_req = AuthorizationRequest(
            trace_id="trc_appr_02",
            subject="operator_bob",
            action="scale_cluster",
            resource="k8s_prod",
        )
        self.auth_decision = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_scale",
            matched_rule_id="rule_scale_appr",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Cluster scaling requires approval.",
        )
        self.appr_req = self.service.create_approval_request(
            self.auth_decision,
            self.auth_req,
            policy_version=2,
        )

    def test_pending_request_cannot_proceed(self) -> None:
        """A pending approval request is never treated as an approval grant."""
        self.assertFalse(self.service.can_proceed(self.appr_req.id))
        self.assertFalse(self.appr_req.is_approved())
        self.assertEqual(self.appr_req.final_effect, PolicyEffect.REQUIRE_APPROVAL)

    def test_explicit_approval_allows_proceed(self) -> None:
        """Authorized human approval transitions status to APPROVED and allows execution."""
        submission = ApprovalDecisionSubmission(
            actor="alice_secops",
            decision=ApprovalDecisionType.APPROVE,
            reason="Verified scaling quota and maintenance schedule.",
        )
        resolved = self.service.submit_decision(self.appr_req.id, submission)

        self.assertEqual(resolved.status, ApprovalStatus.APPROVED)
        self.assertEqual(resolved.final_effect, PolicyEffect.ALLOW)
        self.assertEqual(resolved.decided_by, "alice_secops")
        self.assertEqual(resolved.decision_reason, "Verified scaling quota and maintenance schedule.")
        self.assertIsNotNone(resolved.decided_at)
        self.assertTrue(resolved.is_approved())
        self.assertTrue(resolved.is_terminal())

        # Gating check confirms operation may now proceed
        self.assertTrue(self.service.can_proceed(self.appr_req.id))

    def test_explicit_rejection_blocks_proceed(self) -> None:
        """Authorized human rejection transitions status to REJECTED and blocks execution."""
        submission = ApprovalDecisionSubmission(
            actor="carol_lead",
            decision=ApprovalDecisionType.REJECT,
            reason="Cluster is currently under high load; retry tomorrow.",
        )
        resolved = self.service.submit_decision(self.appr_req.id, submission)

        self.assertEqual(resolved.status, ApprovalStatus.REJECTED)
        self.assertEqual(resolved.final_effect, PolicyEffect.BLOCK)
        self.assertEqual(resolved.decided_by, "carol_lead")
        self.assertFalse(resolved.is_approved())
        self.assertTrue(resolved.is_terminal())

        # Gating check confirms operation cannot proceed
        self.assertFalse(self.service.can_proceed(self.appr_req.id))


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestApprovalSafetyAndSecurityGuards(unittest.TestCase):
    """Verifies security constraints: AI rejection, unauthorized actor blocking, and finality."""

    def setUp(self) -> None:
        self.service = ApprovalService(allowed_approvers=["alice_secops"])
        auth_req = AuthorizationRequest(
            trace_id="trc_appr_03",
            subject="agent_01",
            action="drop_table",
            resource="db_orders",
        )
        auth_decision = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_db_drop",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Destructive DB action requires approval.",
        )
        self.appr_req = self.service.create_approval_request(auth_decision, auth_req, policy_version=1)

    def test_reject_ai_operator_and_llm_approval(self) -> None:
        """AI operators and LLMs are strictly forbidden from approving requests."""
        # Flag-based AI rejection
        with self.assertRaises(ValidationError):
            ApprovalDecisionSubmission(
                actor="claude_operator",
                decision=ApprovalDecisionType.APPROVE,
                is_ai_or_llm=True,
            )

        # Name prefix AI rejection
        with self.assertRaises(ValidationError):
            ApprovalDecisionSubmission(
                actor="ai:security_assistant",
                decision=ApprovalDecisionType.APPROVE,
            )
        with self.assertRaises(ValidationError):
            ApprovalDecisionSubmission(
                actor="llm:gpt4_agent",
                decision=ApprovalDecisionType.APPROVE,
            )

    def test_reject_unauthorized_human_reviewer(self) -> None:
        """Actors not in the authorized approvers registry must be rejected."""
        unauthorized_submission = ApprovalDecisionSubmission(
            actor="eve_unauthorized",
            decision=ApprovalDecisionType.APPROVE,
            reason="I want to approve this.",
        )
        with self.assertRaises(PermissionError):
            self.service.submit_decision(self.appr_req.id, unauthorized_submission)

        # Status must remain PENDING and blocked
        req = self.service.get_approval_request(self.appr_req.id)
        self.assertIsNotNone(req)
        self.assertEqual(req.status, ApprovalStatus.PENDING)  # type: ignore[union-attr]
        self.assertFalse(self.service.can_proceed(self.appr_req.id))

    def test_enforce_single_decision_finality(self) -> None:
        """An already finalized approval cannot be overwritten or contradicted."""
        submission_approve = ApprovalDecisionSubmission(
            actor="alice_secops",
            decision=ApprovalDecisionType.APPROVE,
            reason="Initial approval.",
        )
        self.service.submit_decision(self.appr_req.id, submission_approve)

        # Attempting duplicate approval
        with self.assertRaises(ValueError):
            self.service.submit_decision(self.appr_req.id, submission_approve)

        # Attempting contradictory rejection
        submission_reject = ApprovalDecisionSubmission(
            actor="alice_secops",
            decision=ApprovalDecisionType.REJECT,
            reason="Changed my mind.",
        )
        with self.assertRaises(ValueError):
            self.service.submit_decision(self.appr_req.id, submission_reject)

    def test_nonexistent_approval_id_handling(self) -> None:
        """Nonexistent approval IDs fail safely."""
        self.assertIsNone(self.service.get_approval_request("appr_nonexistent"))
        self.assertFalse(self.service.can_proceed("appr_nonexistent"))
        with self.assertRaises(KeyError):
            self.service.submit_decision(
                "appr_nonexistent",
                ApprovalDecisionSubmission(actor="alice_secops", decision=ApprovalDecisionType.APPROVE),
            )


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyRevisionDriftGuard(unittest.TestCase):
    """Verifies that approvals are invalidated if underlying policy revisions change."""

    def setUp(self) -> None:
        self.repo = PolicyRepository()
        self.rule = RuleDefinition(
            id="rule_sensitive",
            name="Sensitive Access Rule",
            effect=PolicyEffect.REQUIRE_APPROVAL,
            priority=1,
            condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="export_pii"),
        )
        self.policy = self.repo.create_policy(
            PolicyCreate(id="pol_pii", name="PII Export Policy", rules=[self.rule])
        )
        self.service = ApprovalService()

        self.auth_req = AuthorizationRequest(
            trace_id="trc_pii_01",
            subject="data_analyst",
            action="export_pii",
            resource="customer_db",
        )
        # Evaluated at policy revision 1
        self.decision = evaluate_policy(self.policy, self.auth_req)
        self.appr_req = self.service.create_approval_request(
            self.decision,
            self.auth_req,
            policy_version=self.policy.version,
        )

    def test_approval_invalidated_when_policy_version_changes_before_decision(self) -> None:
        """If policy updates to revision 2 before approval, the approval request expires."""
        # Update policy in repository, creating revision 2
        self.repo.update_policy(
            "pol_pii",
            PolicyUpdate(name="PII Export Policy v2 (Strict)"),
            actor="sec_officer",
        )
        current_pol = self.repo.get_policy("pol_pii")
        self.assertEqual(current_pol.version, 2)  # type: ignore[union-attr]

        # Reviewer attempts to approve against outdated revision 1
        submission = ApprovalDecisionSubmission(
            actor="compliance_lead",
            decision=ApprovalDecisionType.APPROVE,
            reason="Looks fine.",
        )
        with self.assertRaises(ValueError):
            self.service.submit_decision(self.appr_req.id, submission, policy_repo=self.repo)

        # Status must be EXPIRED and blocked
        req = self.service.get_approval_request(self.appr_req.id)
        self.assertIsNotNone(req)
        self.assertEqual(req.status, ApprovalStatus.EXPIRED)  # type: ignore[union-attr]
        self.assertEqual(req.final_effect, PolicyEffect.BLOCK)  # type: ignore[union-attr]
        self.assertFalse(self.service.can_proceed(self.appr_req.id, policy_repo=self.repo))

    def test_execution_blocked_if_policy_deactivated_after_approval(self) -> None:
        """If policy is disabled in repository after approval, execution is blocked."""
        submission = ApprovalDecisionSubmission(
            actor="compliance_lead",
            decision=ApprovalDecisionType.APPROVE,
        )
        self.service.submit_decision(self.appr_req.id, submission, policy_repo=self.repo)
        self.assertTrue(self.service.can_proceed(self.appr_req.id, policy_repo=self.repo))

        # Disable policy in repository
        self.repo.update_policy("pol_pii", PolicyUpdate(enabled=False))

        # Now can_proceed MUST return False!
        self.assertFalse(self.service.can_proceed(self.appr_req.id, policy_repo=self.repo))


if __name__ == "__main__":
    unittest.main()
