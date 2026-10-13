"""Unit and integration tests for AMGR database persistence layer.

Verifies SQLAlchemy ORM models, PolicyRepository database session persistence,
PolicyDecisionModel history tracking, and schema migrations following the Member 3 Phase 1 roadmap.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.session import Base
from backend.app.models.policy import PolicyModel
from backend.app.models.policy_decision import PolicyDecisionModel
from backend.app.models.policy_version import PolicyVersionModel
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import PolicyCreate, PolicyUpdate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyChangeType,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


class TestDatabasePersistence(unittest.TestCase):
    """Verifies relational database persistence for policies, versions, and decision history."""

    def setUp(self) -> None:
        # Use an isolated in-memory SQLite database for test execution
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db: Session = self.SessionLocal()

        self.policy_repo = PolicyRepository(db_session=self.db)
        self.decision_repo = DecisionRepository(db_session=self.db)
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_create_policy_persists_to_database(self) -> None:
        """Creating a policy writes both PolicyModel and PolicyVersionModel to the database."""
        policy_in = PolicyCreate(
            id="pol_db_01",
            name="Database persistence policy",
            description="Tests SQL persistence",
            priority=50,
            rules=[
                RuleDefinition(
                    id="rule_db_read",
                    name="Read rule",
                    condition=ConditionRule(
                        field="action",
                        operator=ConditionOperator.EQ,
                        value="query_data",
                    ),
                    effect=PolicyEffect.ALLOW,
                )
            ],
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
        )

        created = self.policy_repo.create_policy(
            policy=policy_in,
            actor="admin_user",
            description="Initial creation",
        )
        self.assertEqual(created.version, 1)

        # Inspect database records directly
        db_policy = self.db.get(PolicyModel, "pol_db_01")
        self.assertIsNotNone(db_policy)
        self.assertEqual(db_policy.name, "Database persistence policy")
        self.assertEqual(db_policy.priority, 50)
        self.assertEqual(db_policy.version, 1)
        self.assertTrue(db_policy.enabled)

        # Inspect version snapshot
        stmt = select(PolicyVersionModel).where(PolicyVersionModel.policy_id == "pol_db_01")
        versions = self.db.scalars(stmt).all()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version, 1)
        self.assertEqual(versions[0].change_type, "CREATED")
        self.assertEqual(versions[0].actor, "admin_user")

    def test_update_policy_creates_immutable_new_version_in_db(self) -> None:
        """Updating a policy increments version and appends a new PolicyVersionModel record."""
        policy_in = PolicyCreate(
            id="pol_db_update",
            name="Version 1 title",
            priority=10,
            rules=[
                RuleDefinition(
                    id="r1",
                    name="Rule 1",
                    condition=ConditionRule(field="action", operator=ConditionOperator.EXISTS),
                    effect=PolicyEffect.ALLOW,
                )
            ],
        )
        self.policy_repo.create_policy(policy_in, actor="creator")

        # Update policy
        updated = self.policy_repo.update_policy(
            "pol_db_update",
            PolicyUpdate(name="Version 2 title", priority=20),
            actor="editor",
            description="Upgraded priority",
        )
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.name, "Version 2 title")

        # Verify DB policy was updated
        db_pol = self.db.get(PolicyModel, "pol_db_update")
        self.assertEqual(db_pol.version, 2)
        self.assertEqual(db_pol.priority, 20)

        # Verify both version snapshots exist in DB
        stmt = (
            select(PolicyVersionModel)
            .where(PolicyVersionModel.policy_id == "pol_db_update")
            .order_by(PolicyVersionModel.version.asc())
        )
        versions = self.db.scalars(stmt).all()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version, 1)
        self.assertEqual(versions[1].version, 2)
        self.assertEqual(versions[1].actor, "editor")
        self.assertEqual(versions[1].change_type, "UPDATED")

    def test_repository_reloads_state_from_existing_database(self) -> None:
        """A new PolicyRepository instance connected to existing database reloads all policies and versions."""
        # Create policy in first session
        policy_in = PolicyCreate(
            id="pol_reload_test",
            name="Reload test",
            priority=100,
            rules=[
                RuleDefinition(
                    id="r_reload",
                    name="Rule reload",
                    condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="run"),
                    effect=PolicyEffect.ALLOW,
                )
            ],
        )
        self.policy_repo.create_policy(policy_in, actor="init_actor")

        # Create a second session and new repository instance
        new_db = self.SessionLocal()
        new_repo = PolicyRepository(db_session=new_db)

        # Policy should be fully available from DB
        pol = new_repo.get_policy("pol_reload_test")
        self.assertIsNotNone(pol)
        self.assertEqual(pol.name, "Reload test")
        self.assertEqual(pol.version, 1)
        self.assertEqual(len(pol.rules), 1)

        # History entries should be restored
        history = new_repo.get_change_history("pol_reload_test")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].actor, "init_actor")

        new_db.close()

    def test_decision_history_persists_separately_from_policies(self) -> None:
        """Authorization evaluations persist into PolicyDecisionModel separately from editable policies."""
        auth_req = AuthorizationRequest(
            trace_id="trc_persist_01",
            subject="operator_bob",
            action="deploy_service",
            resource="payment_k8s",
            context={"cluster": "prod-us-east"},
        )
        auth_dec = AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            policy_id="pol_deny_prod",
            matched_rule_id="r_deny",
            matched_rule_ids=["r_deny"],
            matched_policy_ids=["pol_deny_prod"],
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Production deployment blocked by policy.",
            evaluated_at=datetime.now(timezone.utc),
        )

        dec_id = self.decision_repo.record_decision(auth_req, auth_dec)
        self.assertTrue(dec_id.startswith("dec_"))

        # Verify record in database
        db_dec = self.db.get(PolicyDecisionModel, dec_id)
        self.assertIsNotNone(db_dec)
        self.assertEqual(db_dec.trace_id, "trc_persist_01")
        self.assertEqual(db_dec.action, "deploy_service")
        self.assertEqual(db_dec.resource, "payment_k8s")
        self.assertEqual(db_dec.decision, "BLOCK")
        self.assertEqual(db_dec.policy_id, "pol_deny_prod")
        self.assertEqual(db_dec.reason_code, "RULE_MATCHED")
        self.assertEqual(db_dec.matched_rule_ids, ["r_deny"])

        # Query via repository
        retrieved = self.decision_repo.get_decision(dec_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["trace_id"], "trc_persist_01")

        by_trace = self.decision_repo.list_decisions(trace_id="trc_persist_01")
        self.assertEqual(len(by_trace), 1)
        self.assertEqual(by_trace[0]["id"], dec_id)

    def test_authorization_service_persists_decisions_end_to_end(self) -> None:
        """End-to-end: AuthorizationService automatically records evaluated decisions to database."""
        # Create policy
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_e2e_allow",
                name="E2E policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="r_e2e",
                        name="Allow ping",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="ping",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        req = AuthorizationRequest(
            trace_id="trc_e2e_ping",
            subject="client",
            action="ping",
            resource="health_check",
        )

        decision, appr = self.auth_service.authorize(req)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertIsNone(appr)

        # Verify decision was persisted into database
        stmt = select(PolicyDecisionModel).where(PolicyDecisionModel.trace_id == "trc_e2e_ping")
        db_decisions = self.db.scalars(stmt).all()
        self.assertEqual(len(db_decisions), 1)
        self.assertEqual(db_decisions[0].decision, "ALLOW")
        self.assertEqual(db_decisions[0].policy_id, "pol_e2e_allow")


if __name__ == "__main__":
    unittest.main()
