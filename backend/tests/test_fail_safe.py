"""Focused unit and integration tests for fail-safe error handling and runtime guardrails.

Verifies that:
- Unexpected orchestration errors never produce an ALLOW decision.
- Failed database writes trigger rollback and are not falsely reported as persisted.
- Persistence failures clean up pending approval records to prevent phantoms.
- API endpoints return structured HTTP 500 errors without leaking database internals.
- Invalid requests return structured HTTP 422 validation errors.
- Normal ALLOW, BLOCK, and REQUIRE_APPROVAL workflows remain completely unchanged.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from backend.app.main import create_app
from backend.app.models.policy_decision import PolicyDecisionModel
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import PolicyCreate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService
from backend.tests.test_authorization_api import AsgiTestClient


class TestDecisionRepositoryPersistenceFailSafe(unittest.TestCase):
    """Verifies transactional integrity and failure handling in DecisionRepository."""

    def test_database_write_failure_triggers_rollback_and_raises(self) -> None:
        """When db_session.commit() fails, rollback() is called and RuntimeError is raised."""
        mock_session = MagicMock()
        mock_session.commit.side_effect = OperationalError(
            statement="INSERT INTO policy_decisions ...",
            params={},
            orig=Exception("database connection lost"),
        )

        repo = DecisionRepository(db_session=mock_session)
        request = AuthorizationRequest(
            trace_id="trc_fail_01",
            subject="test_agent",
            action="execute_tool",
            resource="tool://test",
            context={},
        )
        decision = AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Blocked by rule.",
        )

        with self.assertRaises(RuntimeError) as ctx:
            repo.record_decision(request, decision)

        self.assertIn("Failed to persist decision to database", str(ctx.exception))
        # Ensure rollback was called
        mock_session.rollback.assert_called_once()
        # Ensure the unpersisted decision was NOT retained in memory
        self.assertEqual(len(repo._decisions), 0)

    def test_in_memory_mode_persists_safely_when_no_db_session(self) -> None:
        """When db_session is None, in-memory recording works normally."""
        repo = DecisionRepository(db_session=None)
        request = AuthorizationRequest(
            trace_id="trc_mem_01",
            subject="test_agent",
            action="query_db",
            resource="db://test",
            context={},
        )
        decision = AuthorizationDecision(
            decision=PolicyEffect.ALLOW,
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Allowed.",
        )

        decision_id = repo.record_decision(request, decision)
        self.assertTrue(decision_id.startswith("dec_"))
        self.assertEqual(len(repo._decisions), 1)
        self.assertEqual(repo.get_decision(decision_id)["trace_id"], "trc_mem_01")


class TestAuthorizationServiceFailSafe(unittest.TestCase):
    """Verifies that orchestration failures clean up state and never produce ALLOW."""

    def test_persistence_failure_cleans_up_phantom_approval_request(self) -> None:
        """If decision persistence fails on REQUIRE_APPROVAL, the created ApprovalRequest is purged."""
        policy_repo = PolicyRepository()
        policy_repo.create_policy(
            PolicyCreate(
                id="pol_approval_required",
                name="Require approval policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_require_appr",
                        name="Rule requiring approval",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="delete_database",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )

        approval_service = ApprovalService()
        mock_decision_repo = MagicMock()
        mock_decision_repo.record_decision.side_effect = RuntimeError("DB write error")

        auth_service = AuthorizationService(
            policy_repo=policy_repo,
            approval_service=approval_service,
            decision_repo=mock_decision_repo,
        )

        request = AuthorizationRequest(
            trace_id="trc_purge_appr_01",
            subject="db_admin",
            action="delete_database",
            resource="db://prod",
            context={},
        )

        # Persistence failure must bubble up as an error, NOT return ALLOW or REQUIRE_APPROVAL
        with self.assertRaises(RuntimeError):
            auth_service.authorize(request)

        # Verify no phantom approval request was left in approval service
        self.assertEqual(len(approval_service.list_all_requests()), 0)


class TestApiFailSafeBoundary(unittest.TestCase):
    """Verifies fail-safe HTTP boundary behavior under unexpected errors and invalid inputs."""

    def test_unexpected_evaluator_error_returns_500_and_never_allows(self) -> None:
        """When an internal exception occurs during authorization, endpoint returns 500 without leaking details."""
        mock_auth_service = MagicMock()
        mock_auth_service.authorize.side_effect = RuntimeError("Fatal internal engine exception")

        app = create_app(auth_service=mock_auth_service)
        client = AsgiTestClient(app)

        payload = {
            "trace_id": "trc_err_01",
            "subject": "service_account",
            "action": "execute_task",
            "resource": "task://worker",
            "context": {},
        }
        resp = client.post("/api/v1/authorize", json_data=payload)

        # Must return 500 Internal Server Error
        self.assertEqual(resp.status_code, 500)
        data = resp.json()
        self.assertEqual(data["detail"], "Authorization evaluation failed safely.")
        # Must NEVER return decision ALLOW
        self.assertNotIn("decision", data)
        self.assertNotIn("ALLOW", resp.text)
        # Must not leak stack trace or internal error messages
        self.assertNotIn("Fatal internal engine exception", resp.text)

    def test_persistence_failure_on_allow_evaluation_returns_500(self) -> None:
        """Even if the policy outcome would be ALLOW, a persistence failure must fail closed (HTTP 500)."""
        policy_repo = PolicyRepository()
        policy_repo.create_policy(
            PolicyCreate(
                id="pol_allow_always",
                name="Allow policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_allow",
                        name="Allow all",
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

        mock_decision_repo = MagicMock()
        mock_decision_repo.record_decision.side_effect = RuntimeError("Disk full")

        auth_service = AuthorizationService(
            policy_repo=policy_repo,
            decision_repo=mock_decision_repo,
        )

        app = create_app(auth_service=auth_service)
        client = AsgiTestClient(app)

        payload = {
            "trace_id": "trc_allow_persistence_fail",
            "subject": "agent_alpha",
            "action": "ping",
            "resource": "service://health",
            "context": {},
        }
        resp = client.post("/api/v1/authorize", json_data=payload)

        # Must fail closed with 500, not return ALLOW
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("ALLOW", resp.text)
        self.assertEqual(resp.json()["detail"], "Authorization evaluation failed safely.")

    def test_invalid_request_returns_422_with_structured_error(self) -> None:
        """Missing required fields or extra fields return structured 422 Unprocessable Entity."""
        app = create_app()
        client = AsgiTestClient(app)

        # Missing required 'action'
        resp = client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_invalid_01",
                "subject": "agent",
                "resource": "res://test",
            },
        )
        self.assertEqual(resp.status_code, 422)

        # Extra forbidden field
        resp = client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_invalid_02",
                "subject": "agent",
                "action": "read",
                "resource": "res://test",
                "unauthorized_extra": True,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_normal_successful_authorization_paths_preserved(self) -> None:
        """ALLOW, BLOCK, and REQUIRE_APPROVAL function identically with no regressions."""
        policy_repo = PolicyRepository()
        approval_service = ApprovalService()
        auth_service = AuthorizationService(
            policy_repo=policy_repo,
            approval_service=approval_service,
        )

        policy_repo.create_policy(
            PolicyCreate(
                id="pol_rules",
                name="Mixed rules policy",
                priority=50,
                rules=[
                    RuleDefinition(
                        id="rule_allow",
                        name="Allow rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="read",
                        ),
                        effect=PolicyEffect.ALLOW,
                    ),
                    RuleDefinition(
                        id="rule_block",
                        name="Block rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="nuke",
                        ),
                        effect=PolicyEffect.BLOCK,
                    ),
                    RuleDefinition(
                        id="rule_appr",
                        name="Approval rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="modify",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    ),
                ],
            )
        )

        app = create_app(
            policy_repo=policy_repo,
            approval_service=approval_service,
            auth_service=auth_service,
        )
        client = AsgiTestClient(app)

        # 1. ALLOW
        resp_allow = client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_allow_ok",
                "subject": "user",
                "action": "read",
                "resource": "doc://1",
            },
        )
        self.assertEqual(resp_allow.status_code, 200)
        self.assertEqual(resp_allow.json()["decision"], "ALLOW")

        # 2. BLOCK
        resp_block = client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_block_ok",
                "subject": "user",
                "action": "nuke",
                "resource": "sys://all",
            },
        )
        self.assertEqual(resp_block.status_code, 200)
        self.assertEqual(resp_block.json()["decision"], "BLOCK")

        # 3. REQUIRE_APPROVAL
        resp_appr = client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_appr_ok",
                "subject": "user",
                "action": "modify",
                "resource": "doc://1",
            },
        )
        self.assertEqual(resp_appr.status_code, 200)
        self.assertEqual(resp_appr.json()["decision"], "REQUIRE_APPROVAL")
        appr_hdr = resp_appr.headers.get("x-amgr-approval-id") or resp_appr.headers.get("X-AMGR-Approval-ID")
        self.assertIsNotNone(appr_hdr)
        self.assertTrue(appr_hdr.startswith("appr_"))


if __name__ == "__main__":
    unittest.main()
