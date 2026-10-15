"""Comprehensive unit and integration tests for approval relational persistence.

Verifies ApprovalModel, ApprovalRepository, database session persistence,
persistence across service re-instantiation, lifecycle transitions, TTL expiration,
cancellation, drift invalidation, transactional rollback, and backward compatibility.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from backend.tests.test_authorization_api import AsgiTestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.session import Base
from backend.app.main import create_app
from backend.app.models.approval import ApprovalModel
from backend.app.models.policy import PolicyModel
from backend.app.models.policy_version import PolicyVersionModel
from backend.app.repositories.approval_repository import (
    ApprovalPersistenceError,
    ApprovalRepository,
)
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import (
    ApprovalCancellationSubmission,
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
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


class TestApprovalRelationalPersistence(unittest.TestCase):
    """Verifies relational database persistence for human approval requests."""

    def setUp(self) -> None:
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

        # Create a test policy in repository
        self.test_policy = self.policy_repo.create_policy(
            policy=PolicyCreate(
                id="pol_financial_ops",
                name="Financial Operations Policy",
                description="Governs wire transfers",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_require_compliance",
                        name="Require compliance review",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="wire_transfer",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
                resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
                default_effect=PolicyEffect.BLOCK,
                enabled=True,
            ),
            actor="compliance_lead",
            description="Initial policy registration",
        )

        self.auth_req = AuthorizationRequest(
            trace_id="trc_wire_001",
            subject={"agent_id": "payment_agent", "role": "operator"},
            action="wire_transfer",
            resource="payment://wire/swift",
            context={"amount": 75000, "currency": "USD"},
        )

        self.auth_decision = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_financial_ops",
            policy_version=1,
            matched_rule_id="rule_require_compliance",
            matched_rule_ids=["rule_require_compliance"],
            matched_policy_ids=["pol_financial_ops"],
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Wire transfer exceeds autonomous limit; compliance review required.",
        )

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_create_and_retrieve_approval_in_database(self) -> None:
        """Creating an approval request persists an ApprovalModel record into the database."""
        req = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
            ttl_seconds=3600,
        )

        self.assertEqual(req.id, "appr_0001")
        self.assertEqual(req.status, ApprovalStatus.PENDING)
        self.assertEqual(req.final_effect, PolicyEffect.REQUIRE_APPROVAL)

        # Verify record directly in database
        db_model = self.db.get(ApprovalModel, "appr_0001")
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.trace_id, "trc_wire_001")
        self.assertEqual(db_model.policy_id, "pol_financial_ops")
        self.assertEqual(db_model.policy_version, 1)
        self.assertEqual(db_model.matched_rule_id, "rule_require_compliance")
        self.assertEqual(db_model.status, "PENDING")
        self.assertEqual(db_model.final_effect, "REQUIRE_APPROVAL")
        self.assertEqual(db_model.request_payload_data["action"], "wire_transfer")
        self.assertEqual(db_model.original_decision_data["decision"], "REQUIRE_APPROVAL")
        self.assertIsNotNone(db_model.created_at)
        self.assertIsNotNone(db_model.expires_at)

        # Verify retrieval via repository get()
        retrieved = self.approval_repo.get("appr_0001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, "appr_0001")
        self.assertEqual(retrieved.trace_id, "trc_wire_001")
        self.assertEqual(retrieved.status, ApprovalStatus.PENDING)

    def test_update_approval_decision_persisted_to_database(self) -> None:
        """Submitting a human approval decision updates the database record to APPROVED/ALLOW."""
        req = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )

        submission = ApprovalDecisionSubmission(
            actor="officer_sarah",
            decision=ApprovalDecisionType.APPROVE,
            reason="Verified client wire documentation ticket #1024.",
            is_ai_or_llm=False,
        )

        decided = self.approval_service.submit_decision(
            approval_id=req.id,
            submission=submission,
            policy_repo=self.policy_repo,
        )

        self.assertEqual(decided.status, ApprovalStatus.APPROVED)
        self.assertEqual(decided.final_effect, PolicyEffect.ALLOW)
        self.assertEqual(decided.decided_by, "officer_sarah")

        # Inspect database model directly
        db_model = self.db.get(ApprovalModel, req.id)
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.status, "APPROVED")
        self.assertEqual(db_model.final_effect, "ALLOW")
        self.assertEqual(db_model.decided_by, "officer_sarah")
        self.assertEqual(db_model.decision_reason, "Verified client wire documentation ticket #1024.")
        self.assertIsNotNone(db_model.decided_at)

    def test_persistence_across_service_reinstantiation(self) -> None:
        """Approvals and monotonic ID counters persist across service re-instantiation."""
        # Service 1 creates two approvals and approves one
        req1 = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )
        req2 = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )

        self.approval_service.submit_decision(
            approval_id=req1.id,
            submission=ApprovalDecisionSubmission(
                actor="officer_bob",
                decision=ApprovalDecisionType.APPROVE,
                reason="Sign-off",
            ),
            policy_repo=self.policy_repo,
        )

        # Simulate fresh service instance with fresh session connected to same database
        new_db_session: Session = self.SessionLocal()
        new_repo = ApprovalRepository(db_session=new_db_session)
        new_service = ApprovalService(repository=new_repo)

        # Verify previous approvals exist in new service
        loaded_req1 = new_service.get_approval_request("appr_0001")
        self.assertIsNotNone(loaded_req1)
        self.assertEqual(loaded_req1.status, ApprovalStatus.APPROVED)
        self.assertEqual(loaded_req1.final_effect, PolicyEffect.ALLOW)
        self.assertEqual(loaded_req1.decided_by, "officer_bob")

        loaded_req2 = new_service.get_approval_request("appr_0002")
        self.assertIsNotNone(loaded_req2)
        self.assertEqual(loaded_req2.status, ApprovalStatus.PENDING)

        # Creating a third approval must use next monotonic counter (appr_0003)
        req3 = new_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )
        self.assertEqual(req3.id, "appr_0003")

        new_db_session.close()

    def test_ttl_expiration_persisted_to_database(self) -> None:
        """TTL expired approval requests are transitioned to EXPIRED/BLOCK in database."""
        req = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
            ttl_seconds=10,
        )

        # Fast forward time beyond TTL
        future_time = datetime.now(timezone.utc) + timedelta(seconds=30)
        expired_ids = self.approval_service.expire_stale_requests(now=future_time)
        self.assertIn(req.id, expired_ids)

        # Inspect database record
        db_model = self.db.get(ApprovalModel, req.id)
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.status, "EXPIRED")
        self.assertEqual(db_model.final_effect, "BLOCK")

        # can_proceed must fail closed
        self.assertFalse(self.approval_service.can_proceed(req.id, policy_repo=self.policy_repo, now=future_time))

    def test_cancellation_persisted_to_database(self) -> None:
        """Explicit cancellation updates approval to EXPIRED/BLOCK in database."""
        req = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )

        cancelled = self.approval_service.cancel_approval(
            approval_id=req.id,
            actor="security_officer_dan",
            reason="Suspected fraudulent transfer context.",
        )
        self.assertEqual(cancelled.status, ApprovalStatus.EXPIRED)
        self.assertEqual(cancelled.final_effect, PolicyEffect.BLOCK)

        # Inspect DB
        db_model = self.db.get(ApprovalModel, req.id)
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.status, "EXPIRED")
        self.assertEqual(db_model.final_effect, "BLOCK")
        self.assertEqual(db_model.decided_by, "security_officer_dan")
        self.assertEqual(db_model.decision_reason, "Suspected fraudulent transfer context.")

    def test_policy_drift_invalidation_persisted_to_database(self) -> None:
        """When governing policy increments version, approval submission invalidates and expires in DB."""
        req = self.approval_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )

        # Mutate policy to version 2
        self.policy_repo.update_policy(
            policy_id="pol_financial_ops",
            update=PolicyUpdate(description="Updated policy rules for wires"),
            actor="compliance_lead",
        )

        # Attempting to submit decision must raise ValueError and persist EXPIRED state
        with self.assertRaises(ValueError):
            self.approval_service.submit_decision(
                approval_id=req.id,
                submission=ApprovalDecisionSubmission(
                    actor="reviewer_jane",
                    decision=ApprovalDecisionType.APPROVE,
                ),
                policy_repo=self.policy_repo,
            )

        db_model = self.db.get(ApprovalModel, req.id)
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.status, "EXPIRED")
        self.assertEqual(db_model.final_effect, "BLOCK")

    def test_transactional_rollback_on_persistence_failure(self) -> None:
        """Database errors during save raise ApprovalPersistenceError and roll back transaction."""
        mock_session = MagicMock(spec=Session)
        mock_session.commit.side_effect = RuntimeError("Disk full / connection drop")

        failing_repo = ApprovalRepository(db_session=mock_session)
        failing_service = ApprovalService(repository=failing_repo)

        with self.assertRaises(ApprovalPersistenceError):
            failing_service.create_approval_request(
                authorization_decision=self.auth_decision,
                authorization_request=self.auth_req,
                policy_version=1,
            )

        # Verify database session rollback was invoked
        mock_session.rollback.assert_called_once()

    def test_in_memory_mode_operates_without_database(self) -> None:
        """ApprovalService without db_session functions 100% in-memory with full lifecycle."""
        mem_service = ApprovalService(db_session=None)
        req = mem_service.create_approval_request(
            authorization_decision=self.auth_decision,
            authorization_request=self.auth_req,
            policy_version=1,
        )
        self.assertEqual(req.id, "appr_0001")
        self.assertEqual(req.status, ApprovalStatus.PENDING)

        decided = mem_service.submit_decision(
            approval_id=req.id,
            submission=ApprovalDecisionSubmission(
                actor="human_admin",
                decision=ApprovalDecisionType.APPROVE,
            ),
        )
        self.assertEqual(decided.status, ApprovalStatus.APPROVED)
        self.assertTrue(mem_service.can_proceed(req.id))

    def test_api_endpoints_work_with_persisted_approval_service(self) -> None:
        """FastAPI approval endpoints work seamlessly with database-backed ApprovalService."""
        app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
            approval_repo=self.approval_repo,
        )
        client = AsgiTestClient(app)

        # Trigger authorization evaluation that produces REQUIRE_APPROVAL
        resp = client.post("/api/v1/authorize", json_data=self.auth_req.model_dump(mode="json"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "REQUIRE_APPROVAL")
        approval_id = resp.headers.get("x-amgr-approval-id") or resp.headers.get("X-AMGR-Approval-ID")
        self.assertIsNotNone(approval_id)

        # List approvals via API
        list_resp = client.get("/api/v1/approvals")
        self.assertEqual(list_resp.status_code, 200)
        approvals = list_resp.json()
        self.assertTrue(any(a["id"] == approval_id for a in approvals))

        # Check can-proceed via API (must be false while pending)
        proceed_resp = client.get(f"/api/v1/approvals/{approval_id}/can-proceed")
        self.assertEqual(proceed_resp.status_code, 200)
        self.assertFalse(proceed_resp.json()["can_proceed"])

        # Submit human approval via API
        decision_resp = client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json_data={
                "actor": "lead_compliance_officer",
                "decision": "APPROVE",
                "reason": "Wire documentation verified.",
                "is_ai_or_llm": False,
            },
        )
        self.assertEqual(decision_resp.status_code, 200)
        self.assertEqual(decision_resp.json()["status"], "APPROVED")

        # Check can-proceed now true
        proceed_resp2 = client.get(f"/api/v1/approvals/{approval_id}/can-proceed")
        self.assertEqual(proceed_resp2.status_code, 200)
        self.assertTrue(proceed_resp2.json()["can_proceed"])

        # Direct database inspection confirms persistence from API call
        db_model = self.db.get(ApprovalModel, approval_id)
        self.assertIsNotNone(db_model)
        self.assertEqual(db_model.status, "APPROVED")
        self.assertEqual(db_model.final_effect, "ALLOW")


if __name__ == "__main__":
    unittest.main()
