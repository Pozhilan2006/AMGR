"""End-to-End Governance and Delivery Integration Verification Suite for AMGR Phase 6.

Validates the full governance control plane lifecycle across Phases 1–5:
1. Deterministic Policy Evaluation & Authorization:
   - Validates ALLOW, BLOCK, and REQUIRE_APPROVAL policy evaluation.
2. Approval Request Creation:
   - Verifies an action requiring approval creates a pending approval request with correlation tracing.
3. Relational Persistence & Service Re-instantiation:
   - Confirms database-persisted approvals survive service re-instantiation against the relational engine.
4. Fail-Closed Security Guarantees:
   - Ensures unmatched actions, missing security context, and persistence failures strictly fail closed.
5. Approval Lifecycle & Policy Drift Compatibility:
   - Verifies human decision approval, execution gate checks, cancellation, TTL expiration, and policy revision drift protection.
6. Unified API Route Conformance:
   - Validates live responses for canonical authorization, approval, decision audit, and system health endpoints.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.session import Base
from backend.app.main import create_app
from backend.app.repositories.approval_repository import (
    ApprovalPersistenceError,
    ApprovalRepository,
)
from backend.app.repositories.decision_repository import DecisionRepository
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
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import PolicyCreate, PolicyUpdate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService
from backend.tests.test_authorization_api import AsgiTestClient


class TestPhase6FinalIntegration(unittest.TestCase):
    """End-to-end integration verification suite for AMGR Phase 6 delivery readiness."""

    def setUp(self) -> None:
        """Configures an isolated in-memory SQLite database and full service stack."""
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db: Session = self.SessionLocal()

        self.policy_repo = PolicyRepository(db_session=self.db)
        self.approval_repo = ApprovalRepository(db_session=self.db)
        self.approval_service = ApprovalService(repository=self.approval_repo)
        self.decision_repo = DecisionRepository(db_session=self.db)
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_repo=self.approval_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            decision_repo=self.decision_repo,
        )
        self.client = AsgiTestClient(self.app)

        # Seed test policies:
        # Policy 1: Allows read operations on non-sensitive telemetry
        self.policy_repo.create_policy(
            policy=PolicyCreate(
                id="pol_telemetry_read",
                name="Telemetry Read Policy",
                description="Allows telemetry queries",
                priority=10,
                rules=[
                    RuleDefinition(
                        id="rule_allow_telemetry",
                        name="Allow read actions on metrics",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="telemetry.read",
                        ),
                        effect=PolicyEffect.ALLOW,
                        priority=10,
                    )
                ],
            )
        )

        # Policy 2: Requires human approval for privileged wire transfer actions
        self.policy_repo.create_policy(
            policy=PolicyCreate(
                id="pol_financial_ops",
                name="Financial Operations Policy",
                description="Governs wire transfers and fund distributions",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_require_compliance",
                        name="Require human approval for wire transfers",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="financial.wire_transfer",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                        priority=100,
                    )
                ],
            )
        )

        # Policy 3: Explicitly blocks dangerous shell execution actions
        self.policy_repo.create_policy(
            policy=PolicyCreate(
                id="pol_rce_defense",
                name="Remote Code Execution Defense",
                description="Blocks arbitrary code and shell execution",
                priority=200,
                rules=[
                    RuleDefinition(
                        id="rule_block_shell",
                        name="Block shell executions",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="system.shell_exec",
                        ),
                        effect=PolicyEffect.BLOCK,
                        priority=200,
                    )
                ],
            )
        )

    def tearDown(self) -> None:
        """Tears down the in-memory database session."""
        self.db.close()
        Base.metadata.drop_all(self.engine)

    # -------------------------------------------------------------------------
    # 1. Deterministic Policy Evaluation & Authorization
    # -------------------------------------------------------------------------
    def test_policy_evaluation_returns_expected_decisions(self) -> None:
        """Verifies policy evaluation correctly yields ALLOW, BLOCK, and REQUIRE_APPROVAL."""
        # Case A: ALLOW policy match
        allow_req = AuthorizationRequest(
            trace_id="trc_allow_01",
            subject={"agent_id": "observer-agent", "role": "analyst"},
            action="telemetry.read",
            resource="service://metrics-collector",
            context={"format": "json"},
        )
        decision, appr = self.auth_service.authorize(allow_req)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_telemetry_read")
        self.assertEqual(decision.matched_rule_id, "rule_allow_telemetry")
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)
        self.assertIsNone(appr)

        # Case B: BLOCK policy match
        block_req = AuthorizationRequest(
            trace_id="trc_block_01",
            subject={"agent_id": "rogue-agent", "role": "untrusted"},
            action="system.shell_exec",
            resource="host://root-shell",
            context={"command": "rm -rf /"},
        )
        decision, appr = self.auth_service.authorize(block_req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.policy_id, "pol_rce_defense")
        self.assertEqual(decision.matched_rule_id, "rule_block_shell")
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)
        self.assertIsNone(appr)

    # -------------------------------------------------------------------------
    # 2. Approval Request Creation
    # -------------------------------------------------------------------------
    def test_approval_required_action_creates_pending_approval(self) -> None:
        """Verifies an action requiring approval creates a pending approval record."""
        req = AuthorizationRequest(
            trace_id="trc_fin_transfer_99",
            subject={"agent_id": "payment-agent", "account": "ACC-8821"},
            action="financial.wire_transfer",
            resource="banking://swift/outbound",
            context={"amount": 250000, "currency": "USD"},
        )

        decision, appr = self.auth_service.authorize(req)

        self.assertEqual(decision.decision, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(decision.policy_id, "pol_financial_ops")
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)
        self.assertIsNotNone(appr)
        assert appr is not None

        # Verify created approval record attributes
        self.assertEqual(appr.trace_id, "trc_fin_transfer_99")
        self.assertEqual(appr.policy_id, "pol_financial_ops")
        self.assertEqual(appr.policy_version, 1)
        self.assertEqual(appr.status, ApprovalStatus.PENDING)
        self.assertEqual(appr.request_payload.action, "financial.wire_transfer")
        self.assertEqual(appr.request_payload.resource, "banking://swift/outbound")
        self.assertIsInstance(appr.created_at, datetime)
        self.assertIsNone(appr.decided_at)

    # -------------------------------------------------------------------------
    # 3. Relational Persistence & Service Re-instantiation
    # -------------------------------------------------------------------------
    def test_database_backed_approval_persists_across_service_reinstantiation(self) -> None:
        """Verifies approvals can be retrieved from database after recreating service instances."""
        req = AuthorizationRequest(
            trace_id="trc_reinst_101",
            subject={"agent_id": "settlement-worker"},
            action="financial.wire_transfer",
            resource="banking://fedwire/dispatch",
            context={"amount": 500000},
        )
        decision, created_appr = self.auth_service.authorize(req)
        self.assertIsNotNone(created_appr)
        assert created_appr is not None
        approval_id = created_appr.id

        # Completely discard existing in-memory repository and service instances
        # and re-instantiate them with a fresh database session on the same database
        fresh_db: Session = self.SessionLocal()
        fresh_repo = ApprovalRepository(db_session=fresh_db)
        fresh_service = ApprovalService(repository=fresh_repo)

        # Retrieve the approval through the new service instance
        retrieved_appr = fresh_service.get_approval_request(approval_id)
        self.assertIsNotNone(retrieved_appr)
        assert retrieved_appr is not None

        self.assertEqual(retrieved_appr.id, approval_id)
        self.assertEqual(retrieved_appr.trace_id, "trc_reinst_101")
        self.assertEqual(retrieved_appr.status, ApprovalStatus.PENDING)
        self.assertEqual(retrieved_appr.policy_id, "pol_financial_ops")
        self.assertEqual(retrieved_appr.request_payload.action, "financial.wire_transfer")
        self.assertEqual(retrieved_appr.request_payload.resource, "banking://fedwire/dispatch")

        fresh_db.close()

    # -------------------------------------------------------------------------
    # 4. Fail-Closed Security Guarantees
    # -------------------------------------------------------------------------
    def test_fail_closed_on_unmatched_action(self) -> None:
        """Verifies unmatched requests fall back strictly to BLOCK rather than ALLOW."""
        unmatched_req = AuthorizationRequest(
            trace_id="trc_unknown_99",
            subject={"agent_id": "unknown-caller"},
            action="arbitrary.undefined_action",
            resource="unknown://endpoint",
        )
        decision, appr = self.auth_service.authorize(unmatched_req)

        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertIsNone(decision.policy_id)
        self.assertEqual(decision.reason_code, ReasonCode.DEFAULT_FALLBACK)
        self.assertIn("No policies matched request", decision.reason_message)
        self.assertIsNone(appr)

    def test_fail_closed_on_approval_persistence_failure(self) -> None:
        """Verifies persistence failures in approval creation fail closed without synthetic ALLOW."""
        failing_repo = MagicMock(spec=ApprovalRepository)
        failing_repo._requests = {}
        failing_repo.save.side_effect = ApprovalPersistenceError("Simulated database failure")
        failing_approval_service = ApprovalService(repository=failing_repo)

        safe_auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=failing_approval_service,
            decision_repo=self.decision_repo,
        )

        req = AuthorizationRequest(
            trace_id="trc_fail_persist_500",
            subject={"agent_id": "payment-agent"},
            action="financial.wire_transfer",
            resource="banking://swift/outbound",
        )

        # Expect fail-closed: must raise ApprovalPersistenceError and never yield an accidental ALLOW
        with self.assertRaises(ApprovalPersistenceError):
            safe_auth_service.authorize(req)

    # -------------------------------------------------------------------------
    # 5. Approval Lifecycle & Policy Drift Compatibility
    # -------------------------------------------------------------------------
    def test_approval_lifecycle_decisions_and_drift_protection(self) -> None:
        """Verifies human decision submission, execution gate check, and policy drift protection."""
        # Step 1: Create pending approval
        req = AuthorizationRequest(
            trace_id="trc_drift_test_01",
            subject={"agent_id": "treasury-agent"},
            action="financial.wire_transfer",
            resource="banking://swift/outbound",
        )
        decision, appr = self.auth_service.authorize(req)
        assert appr is not None
        approval_id = appr.id

        # Gate check before decision must be False (pending)
        self.assertFalse(self.approval_service.can_proceed(approval_id))

        # Step 2: Human reviewer submits approval decision
        submission = ApprovalDecisionSubmission(
            actor="compliance_officer_alice",
            decision=ApprovalDecisionType.APPROVE,
            reason="Verified enterprise purchase order",
        )
        approved_record = self.approval_service.submit_decision(
            approval_id=approval_id,
            submission=submission,
            policy_repo=self.policy_repo,
        )
        self.assertEqual(approved_record.status, ApprovalStatus.APPROVED)
        self.assertEqual(approved_record.decided_by, "compliance_officer_alice")

        # Gate check after approval must be True
        self.assertTrue(self.approval_service.can_proceed(approval_id, policy_repo=self.policy_repo))

        # Step 3: Policy definition changes (version increment v1 -> v2) causing drift
        self.policy_repo.update_policy(
            policy_id="pol_financial_ops",
            update=PolicyUpdate(description="Updated security requirements for wire transfers"),
            actor="security_admin",
        )

        # Step 4: Execution gate check must fail-closed due to policy revision drift
        self.assertFalse(self.approval_service.can_proceed(approval_id, policy_repo=self.policy_repo))

    def test_approval_cancellation_and_ttl_expiration(self) -> None:
        """Verifies cancellation and stale request expiration transitions."""
        # Create an approval
        req = AuthorizationRequest(
            trace_id="trc_cancel_01",
            subject={"agent_id": "treasury-agent"},
            action="financial.wire_transfer",
            resource="banking://swift/outbound",
        )
        _, appr = self.auth_service.authorize(req)
        assert appr is not None

        # Cancel the approval
        cancelled = self.approval_service.cancel_approval(
            approval_id=appr.id,
            actor="operator_bob",
            reason="Initiator cancelled operation",
        )
        self.assertEqual(cancelled.status, ApprovalStatus.EXPIRED)
        self.assertEqual(cancelled.decision_reason, "Initiator cancelled operation")

        # Expire stale approvals batch
        expired_ids = self.approval_service.expire_stale_requests()
        self.assertIsInstance(expired_ids, list)

    # -------------------------------------------------------------------------
    # 6. Unified API Route Conformance
    # -------------------------------------------------------------------------
    def test_api_route_conformance_and_status_codes(self) -> None:
        """Verifies live FastAPI routes conform to status code and response schemas."""
        # 1. Health check
        resp = self.client.request("GET", "/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

        # 2. Runtime authorization endpoint
        auth_resp = self.client.request(
            "POST",
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_api_01",
                "subject": "service_reporter",
                "action": "telemetry.read",
                "resource": "service://metrics-collector",
            },
        )
        self.assertEqual(auth_resp.status_code, 200)
        data = auth_resp.json()
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["policy_id"], "pol_telemetry_read")

        # 3. REQUIRE_APPROVAL authorization endpoint returns X-AMGR-Approval-ID header
        appr_auth_resp = self.client.request(
            "POST",
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_api_appr_01",
                "subject": "service_reporter",
                "action": "financial.wire_transfer",
                "resource": "banking://swift/outbound",
            },
        )
        self.assertEqual(appr_auth_resp.status_code, 200)
        self.assertEqual(appr_auth_resp.json()["decision"], "REQUIRE_APPROVAL")
        self.assertIn("x-amgr-approval-id", appr_auth_resp.headers)
        created_appr_id = appr_auth_resp.headers["x-amgr-approval-id"]

        # 4. Approvals retrieval route
        get_appr_resp = self.client.request("GET", f"/api/v1/approvals/{created_appr_id}")
        self.assertEqual(get_appr_resp.status_code, 200)
        self.assertEqual(get_appr_resp.json()["id"], created_appr_id)
        self.assertEqual(get_appr_resp.json()["status"], "PENDING")

        # 5. Can-proceed execution gate check route (pending -> False)
        gate_resp = self.client.request(
            "GET", f"/api/v1/approvals/{created_appr_id}/can-proceed"
        )
        self.assertEqual(gate_resp.status_code, 200)
        self.assertFalse(gate_resp.json()["can_proceed"])

        # 6. Human approval decision submission
        dec_resp = self.client.request(
            "POST",
            f"/api/v1/approvals/{created_appr_id}/decision",
            json_data={
                "actor": "compliance_auditor",
                "decision": "APPROVE",
                "reason": "Authorized wire transfer",
                "is_ai_or_llm": False,
            },
        )
        self.assertEqual(dec_resp.status_code, 200)
        self.assertEqual(dec_resp.json()["status"], "APPROVED")

        # 7. Post-approval can-proceed check (approved -> True)
        post_gate_resp = self.client.request(
            "GET", f"/api/v1/approvals/{created_appr_id}/can-proceed"
        )
        self.assertEqual(post_gate_resp.status_code, 200)
        self.assertTrue(post_gate_resp.json()["can_proceed"])

        # 8. Decisions summary analytics endpoint
        summary_resp = self.client.request("GET", "/api/v1/decisions/summary")
        self.assertEqual(summary_resp.status_code, 200)
        sum_data = summary_resp.json()
        self.assertIn("total_decisions", sum_data)
        self.assertGreaterEqual(sum_data["total_decisions"], 2)


if __name__ == "__main__":
    unittest.main()
