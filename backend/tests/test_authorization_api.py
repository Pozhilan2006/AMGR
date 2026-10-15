"""Unit and integration tests for AMGR authorization API and governance workflows.

Tests runtime authorization evaluation endpoints, approval workflow integration,
fail-closed security fallbacks, and anti-tamper guardrails following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from urllib.parse import urlencode

from backend.app.main import create_app
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import ApprovalStatus
from backend.app.schemas.authorization import ReasonCode
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import PolicyCreate, PolicyUpdate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


class TestResponse:
    """Wraps an ASGI HTTP response for assertions."""

    __test__ = False

    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


class AsgiTestClient:
    """Synchronous in-memory test client invoking FastAPI ASGI application directly."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        raw_body = (
            json.dumps(json_data).encode("utf-8") if json_data is not None else b""
        )
        query_string = urlencode(params or {}).encode("utf-8")

        headers_list: list[tuple[bytes, bytes]] = []
        if json_data is not None:
            headers_list.append((b"content-type", b"application/json"))
            headers_list.append((b"content-length", str(len(raw_body)).encode("utf-8")))
        if headers:
            for k, v in headers.items():
                headers_list.append((k.lower().encode("utf-8"), v.encode("utf-8")))

        status_code = 500
        resp_headers: dict[str, str] = {}
        resp_body: list[bytes] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw_body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                for k, v in message.get("headers", []):
                    resp_headers[k.decode("utf-8").lower()] = v.decode("utf-8")
            elif message["type"] == "http.response.body":
                resp_body.append(message.get("body", b""))

        scope: dict[str, Any] = {
            "type": "http",
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string,
            "headers": headers_list,
            "app": self.app,
        }

        asyncio.run(self.app(scope, receive, send))
        return TestResponse(status_code, resp_headers, b"".join(resp_body))

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        return self.request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        return self.request(
            "POST", path, params=params, json_data=json_data, headers=headers
        )


class TestHealthEndpoints(unittest.TestCase):
    """Verifies health check endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = AsgiTestClient(self.app)

    def test_root_health(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_api_v1_health(self) -> None:
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class TestAuthorizationEndpoint(unittest.TestCase):
    """Verifies /api/v1/authorize behavior across deterministic policy outcomes."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
        )
        self.client = AsgiTestClient(self.app)

        # Policy 1: Allows read queries
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_read_allow",
                name="Allow read queries",
                priority=50,
                rules=[
                    RuleDefinition(
                        id="rule_read",
                        name="Read rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="read_query",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        # Policy 2: Requires approval for db migrations
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_migration_require_appr",
                name="Require approval for database migrations",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_migrate",
                        name="Migration rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="run_migration",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )

        # Policy 3: Blocks shell execution
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_block_shell",
                name="Block arbitrary shell commands",
                priority=200,
                rules=[
                    RuleDefinition(
                        id="rule_shell",
                        name="Shell command rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="exec_shell",
                        ),
                        effect=PolicyEffect.BLOCK,
                    )
                ],
            )
        )

    def test_authorize_allow_outcome(self) -> None:
        """Read queries match pol_read_allow and evaluate to ALLOW."""
        payload = {
            "trace_id": "trc_allow_01",
            "subject": "service_agent",
            "action": "read_query",
            "resource": "users_table",
            "context": {"environment": "production"},
        }
        resp = self.client.post("/api/v1/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["policy_id"], "pol_read_allow")
        self.assertEqual(data["matched_rule_id"], "rule_read")
        self.assertEqual(data["reason_code"], "RULE_MATCHED")

    def test_authorize_block_outcome(self) -> None:
        """Shell execution matches pol_block_shell and evaluates to BLOCK."""
        payload = {
            "trace_id": "trc_block_01",
            "subject": "service_agent",
            "action": "exec_shell",
            "resource": "/bin/bash",
            "context": {},
        }
        resp = self.client.post("/api/v1/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["policy_id"], "pol_block_shell")
        self.assertEqual(data["matched_rule_id"], "rule_shell")
        self.assertEqual(data["reason_code"], "RULE_MATCHED")

    def test_authorize_require_approval_creates_request_and_header(self) -> None:
        """Database migration matches REQUIRE_APPROVAL, sets header, and creates approval record."""
        payload = {
            "trace_id": "trc_migrate_01",
            "subject": "db_deployer",
            "action": "run_migration",
            "resource": "production_postgres",
            "context": {"migration_file": "v2_schema.sql"},
        }
        resp = self.client.post("/api/v1/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(data["policy_id"], "pol_migration_require_appr")

        # Verifies header was attached
        approval_id = resp.headers.get("x-amgr-approval-id")
        self.assertIsNotNone(approval_id)
        self.assertTrue(approval_id.startswith("appr_"))

        # Verifies ApprovalRequest was registered in ApprovalService
        appr_req = self.approval_service.get_approval_request(approval_id)
        self.assertIsNotNone(appr_req)
        self.assertEqual(appr_req.trace_id, "trc_migrate_01")
        self.assertEqual(appr_req.status, ApprovalStatus.PENDING)
        self.assertEqual(appr_req.final_effect, PolicyEffect.REQUIRE_APPROVAL)

    def test_authorize_no_matching_policy_fallback_block(self) -> None:
        """Unmatched action falls back closed to BLOCK."""
        payload = {
            "trace_id": "trc_unmatched_01",
            "subject": "unknown_actor",
            "action": "unknown_action",
            "resource": "unknown_res",
        }
        resp = self.client.post("/api/v1/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["reason_code"], "DEFAULT_FALLBACK")

    def test_authorize_all_policies_disabled_fallback_block(self) -> None:
        """When all policies are disabled, fails closed to BLOCK."""
        empty_repo = PolicyRepository()
        empty_app = create_app(policy_repo=empty_repo)
        empty_client = AsgiTestClient(empty_app)

        payload = {
            "trace_id": "trc_empty_01",
            "subject": "actor",
            "action": "read_query",
            "resource": "users_table",
        }
        resp = empty_client.post("/api/v1/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["reason_code"], "DEFAULT_FALLBACK")

    def test_authorize_validation_errors(self) -> None:
        """Missing required fields or extra fields return 422 Unprocessable Entity."""
        # Missing action
        resp = self.client.post(
            "/api/v1/authorize",
            json_data={"trace_id": "trc_01", "subject": "a", "resource": "r"},
        )
        self.assertEqual(resp.status_code, 422)

        # Empty action
        resp = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_01",
                "subject": "a",
                "action": "   ",
                "resource": "r",
            },
        )
        self.assertEqual(resp.status_code, 422)

        # Extra forbidden fields (extra='forbid')
        resp = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_01",
                "subject": "a",
                "action": "read_query",
                "resource": "r",
                "unauthorized_field": "injected",
            },
        )
        self.assertEqual(resp.status_code, 422)


class TestApprovalWorkflowApi(unittest.TestCase):
    """Verifies end-to-end human approval workflow through HTTP API."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService(
            allowed_approvers=["alice_admin", "charlie_lead"]
        )
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
        )
        self.client = AsgiTestClient(self.app)

        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_export_approval",
                name="Export requires human lead approval",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_export",
                        name="Export rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="export_customer_data",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )

    def test_complete_approval_lifecycle(self) -> None:
        """Full lifecycle: Trigger REQUIRE_APPROVAL -> inspect -> approve -> re-authorize -> ALLOW."""
        # 1. Trigger authorization requiring approval
        auth_payload = {
            "trace_id": "trc_export_01",
            "subject": "analytics_bot",
            "action": "export_customer_data",
            "resource": "s3://exports/customers.csv",
            "context": {"records_count": 5000},
        }
        resp = self.client.post("/api/v1/authorize", json_data=auth_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["decision"], "REQUIRE_APPROVAL")
        appr_id = resp.headers["x-amgr-approval-id"]

        # 2. Query approval details by ID
        resp_get = self.client.get(f"/api/v1/approvals/{appr_id}")
        self.assertEqual(resp_get.status_code, 200)
        appr_data = resp_get.json()
        self.assertEqual(appr_data["id"], appr_id)
        self.assertEqual(appr_data["status"], "PENDING")
        self.assertEqual(appr_data["policy_id"], "pol_export_approval")

        # 3. List pending approvals
        resp_list = self.client.get("/api/v1/approvals", params={"status": "PENDING"})
        self.assertEqual(resp_list.status_code, 200)
        items = resp_list.json()
        self.assertTrue(any(i["id"] == appr_id for i in items))

        # 4. Check can-proceed before approval (must be False)
        resp_check = self.client.get(f"/api/v1/approvals/{appr_id}/can-proceed")
        self.assertEqual(resp_check.status_code, 200)
        self.assertFalse(resp_check.json()["can_proceed"])

        # 5. Reviewer submits explicit APPROVE decision
        decision_payload = {
            "actor": "alice_admin",
            "decision": "APPROVE",
            "reason": "Authorized for monthly analytics compliance.",
            "is_ai_or_llm": False,
        }
        resp_dec = self.client.post(
            f"/api/v1/approvals/{appr_id}/decision", json_data=decision_payload
        )
        self.assertEqual(resp_dec.status_code, 200)
        dec_data = resp_dec.json()
        self.assertEqual(dec_data["status"], "APPROVED")
        self.assertEqual(dec_data["final_effect"], "ALLOW")
        self.assertEqual(dec_data["decided_by"], "alice_admin")

        # 6. Check can-proceed after approval (must now be True)
        resp_check_after = self.client.get(f"/api/v1/approvals/{appr_id}/can-proceed")
        self.assertEqual(resp_check_after.status_code, 200)
        self.assertTrue(resp_check_after.json()["can_proceed"])

        # 7. Re-authorize with approval_id in context: evaluates to ALLOW!
        retry_payload = {
            "trace_id": "trc_export_01",
            "subject": "analytics_bot",
            "action": "export_customer_data",
            "resource": "s3://exports/customers.csv",
            "context": {"records_count": 5000, "approval_id": appr_id},
        }
        resp_retry = self.client.post("/api/v1/authorize", json_data=retry_payload)
        self.assertEqual(resp_retry.status_code, 200)
        self.assertEqual(resp_retry.json()["decision"], "ALLOW")
        self.assertIn("Human approval", resp_retry.json()["reason_message"])

    def test_rejection_lifecycle(self) -> None:
        """Rejection lifecycle: Trigger -> reject -> re-authorize -> BLOCK."""
        auth_payload = {
            "trace_id": "trc_export_02",
            "subject": "analytics_bot",
            "action": "export_customer_data",
            "resource": "s3://exports/customers.csv",
        }
        resp = self.client.post("/api/v1/authorize", json_data=auth_payload)
        appr_id = resp.headers["x-amgr-approval-id"]

        # Human reviewer rejects
        resp_dec = self.client.post(
            f"/api/v1/approvals/{appr_id}/decision",
            json_data={
                "actor": "charlie_lead",
                "decision": "REJECT",
                "reason": "Suspicious export parameters.",
            },
        )
        self.assertEqual(resp_dec.status_code, 200)
        self.assertEqual(resp_dec.json()["status"], "REJECTED")
        self.assertEqual(resp_dec.json()["final_effect"], "BLOCK")

        # can-proceed must be False
        resp_check = self.client.get(f"/api/v1/approvals/{appr_id}/can-proceed")
        self.assertFalse(resp_check.json()["can_proceed"])

        # Re-authorizing with rejected approval_id must evaluate to BLOCK
        retry_payload = {
            "trace_id": "trc_export_02",
            "subject": "analytics_bot",
            "action": "export_customer_data",
            "resource": "s3://exports/customers.csv",
            "context": {"approval_id": appr_id},
        }
        resp_retry = self.client.post("/api/v1/authorize", json_data=retry_payload)
        self.assertEqual(resp_retry.status_code, 200)
        self.assertEqual(resp_retry.json()["decision"], "BLOCK")


class TestSecurityAndGovernanceGuardrails(unittest.TestCase):
    """Verifies strict security constraints: AI blocking, finality, and drift detection."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService(allowed_approvers=["alice_admin"])
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
        )
        self.client = AsgiTestClient(self.app)

        self.policy = self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_sensitive_op",
                name="Sensitive operations",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_sensitive",
                        name="Sensitive rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="delete_db",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )

        resp = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_guard_01",
                "subject": "operator",
                "action": "delete_db",
                "resource": "prod_db",
            },
        )
        self.appr_id = resp.headers["x-amgr-approval-id"]

    def test_prevent_ai_flag_approval(self) -> None:
        """Automated AI flag is_ai_or_llm=True is rejected."""
        resp = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={
                "actor": "alice_admin",
                "decision": "APPROVE",
                "is_ai_or_llm": True,
            },
        )
        # Schema validator rejects is_ai_or_llm=True with 422
        self.assertIn(resp.status_code, (403, 422))

    def test_prevent_ai_actor_prefix_approval(self) -> None:
        """Actors starting with ai: or llm: are rejected."""
        for prefix in ("ai:gpt_agent", "llm:claude_bot"):
            resp = self.client.post(
                f"/api/v1/approvals/{self.appr_id}/decision",
                json_data={
                    "actor": prefix,
                    "decision": "APPROVE",
                    "is_ai_or_llm": False,
                },
            )
            self.assertIn(resp.status_code, (403, 422))

    def test_reject_unauthorized_human_reviewer(self) -> None:
        """Human reviewer not in allowed approvers whitelist receives 403 Forbidden."""
        resp = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={
                "actor": "unauthorized_stranger",
                "decision": "APPROVE",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_single_decision_finality(self) -> None:
        """Already finalized approval cannot be overwritten (409 Conflict)."""
        # First decision succeeds
        resp1 = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={"actor": "alice_admin", "decision": "APPROVE"},
        )
        self.assertEqual(resp1.status_code, 200)

        # Contradictory second decision rejected
        resp2 = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={"actor": "alice_admin", "decision": "REJECT"},
        )
        self.assertEqual(resp2.status_code, 409)

    def test_policy_revision_drift_invalidates_approval(self) -> None:
        """If policy updates to revision 2 before approval, submitting decision returns 409 and expires."""
        # Mutate policy in repository
        self.policy_repo.update_policy(
            "pol_sensitive_op",
            PolicyUpdate(name="Sensitive operations v2"),
            actor="secops",
        )

        # Attempt to approve
        resp = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={"actor": "alice_admin", "decision": "APPROVE"},
        )
        self.assertEqual(resp.status_code, 409)

        # Approval status must now be EXPIRED and blocked
        appr = self.approval_service.get_approval_request(self.appr_id)
        self.assertEqual(appr.status, ApprovalStatus.EXPIRED)
        self.assertEqual(appr.final_effect, PolicyEffect.BLOCK)

    def test_nonexistent_approval_endpoints(self) -> None:
        """Nonexistent approval IDs return 404 Not Found."""
        resp_get = self.client.get("/api/v1/approvals/appr_nonexistent")
        self.assertEqual(resp_get.status_code, 404)

        resp_dec = self.client.post(
            "/api/v1/approvals/appr_nonexistent/decision",
            json_data={"actor": "alice_admin", "decision": "APPROVE"},
        )
        self.assertEqual(resp_dec.status_code, 404)

        resp_chk = self.client.get("/api/v1/approvals/appr_nonexistent/can-proceed")
        self.assertEqual(resp_chk.status_code, 404)

        # In authorize context: nonexistent approval_id fails closed to BLOCK
        resp_auth = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_invalid_appr",
                "subject": "actor",
                "action": "delete_db",
                "resource": "prod_db",
                "context": {"approval_id": "appr_nonexistent"},
            },
        )
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.json()["decision"], "BLOCK")
        self.assertEqual(resp_auth.json()["reason_code"], "INVALID_REQUEST")

    def test_policy_drift_after_approval_fails_closed(self) -> None:
        """If policy updates to revision 2 AFTER approval was granted, authorization fails closed to BLOCK."""
        # Approve the request at revision 1
        resp_appr = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={"actor": "alice_admin", "decision": "APPROVE"},
        )
        self.assertEqual(resp_appr.status_code, 200)

        # Policy mutates to revision 2
        self.policy_repo.update_policy(
            "pol_sensitive_op",
            PolicyUpdate(description="Altered security context"),
            actor="secops",
        )

        # Retrying authorization with previously granted approval_id fails closed to BLOCK
        resp_auth = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_guard_02",
                "subject": "operator",
                "action": "delete_db",
                "resource": "prod_db",
                "context": {"approval_id": self.appr_id},
            },
        )
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.json()["decision"], "BLOCK")

        # can-proceed endpoint also reports False
        resp_chk = self.client.get(f"/api/v1/approvals/{self.appr_id}/can-proceed")
        self.assertEqual(resp_chk.status_code, 200)
        self.assertFalse(resp_chk.json()["can_proceed"])

    def test_policy_deactivated_after_approval_fails_closed(self) -> None:
        """If policy is disabled in repository after approval, execution is blocked with POLICY_DISABLED."""
        # Approve request
        resp_appr = self.client.post(
            f"/api/v1/approvals/{self.appr_id}/decision",
            json_data={"actor": "alice_admin", "decision": "APPROVE"},
        )
        self.assertEqual(resp_appr.status_code, 200)

        # Disable policy
        self.policy_repo.update_policy(
            "pol_sensitive_op",
            PolicyUpdate(enabled=False),
            actor="secops",
        )

        # Re-authorizing fails closed to BLOCK
        resp_auth = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_guard_03",
                "subject": "operator",
                "action": "delete_db",
                "resource": "prod_db",
                "context": {"approval_id": self.appr_id},
            },
        )
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.json()["decision"], "BLOCK")
        self.assertEqual(resp_auth.json()["reason_code"], "POLICY_DISABLED")


class TestMultiPolicyAndRouting(unittest.TestCase):
    """Verifies multi-policy priority and conflict resolution and route prefix compatibility."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
        )
        self.client = AsgiTestClient(self.app)

    def test_multi_policy_priority_resolution_via_api(self) -> None:
        """High priority policy (priority 200, BLOCK) overrides lower priority policy (priority 100, ALLOW)."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_low_allow",
                name="Allow all read access",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_allow",
                        name="Allow rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="access_data",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_high_block",
                name="Block sensitive resource",
                priority=200,
                rules=[
                    RuleDefinition(
                        id="rule_block",
                        name="Block rule",
                        condition=ConditionRule(
                            field="resource",
                            operator=ConditionOperator.EQ,
                            value="classified_data",
                        ),
                        effect=PolicyEffect.BLOCK,
                    )
                ],
            )
        )

        # Both match: priority 200 must win
        resp = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_prio_01",
                "subject": "analyst",
                "action": "access_data",
                "resource": "classified_data",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["policy_id"], "pol_high_block")

    def test_multi_policy_conflict_resolution_via_api(self) -> None:
        """Equal priority policies resolve conflicts via BLOCK_OVERRIDES."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_equal_allow",
                name="Allow ops",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="r_allow",
                        name="Allow",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="invoke_service",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_equal_block",
                name="Block ops",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="r_block",
                        name="Block",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="invoke_service",
                        ),
                        effect=PolicyEffect.BLOCK,
                    )
                ],
            )
        )

        resp = self.client.post(
            "/api/v1/authorize",
            json_data={
                "trace_id": "trc_conflict_01",
                "subject": "service_caller",
                "action": "invoke_service",
                "resource": "payment_gateway",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["reason_code"], "CONFLICT_RESOLVED")

    def test_root_route_compatibility(self) -> None:
        """Routes are accessible at root without /api/v1 prefix."""
        resp = self.client.get("/approvals")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_approvals_status_query_filtering(self) -> None:
        """Approvals endpoint filters accurately by status query parameter."""
        # Create an approval request directly
        auth_req = {
            "trace_id": "trc_list_01",
            "subject": "agent",
            "action": "do_task",
            "resource": "res",
        }
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_require_lead",
                name="Require lead approval",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="r_appr",
                        name="Approval rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="do_task",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )
        resp_auth = self.client.post("/api/v1/authorize", json_data=auth_req)
        appr_id = resp_auth.headers["x-amgr-approval-id"]

        # Filter by PENDING
        resp_pending = self.client.get("/api/v1/approvals", params={"status": "PENDING"})
        self.assertEqual(resp_pending.status_code, 200)
        self.assertTrue(any(a["id"] == appr_id for a in resp_pending.json()))

        # Filter by APPROVED (should be empty for this ID)
        resp_approved = self.client.get("/api/v1/approvals", params={"status": "APPROVED"})
        self.assertEqual(resp_approved.status_code, 200)
        self.assertFalse(any(a["id"] == appr_id for a in resp_approved.json()))

    def test_no_duplicate_operation_id_warnings_in_openapi(self) -> None:
        """Verifies OpenAPI schema generation is free of duplicate operation ID warnings and /approvals schema is authoritative."""
        import warnings

        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            schema = self.app.openapi()

        duplicate_warnings = [
            w for w in recorded_warnings if "Duplicate Operation ID" in str(w.message)
        ]
        self.assertEqual(
            len(duplicate_warnings),
            0,
            f"Expected 0 duplicate operation ID warnings, found {len(duplicate_warnings)}: {[str(w.message) for w in duplicate_warnings]}",
        )

        # Verify /api/v1/approvals schema reflects the canonical endpoint with query parameters and response schema
        approvals_path = schema.get("paths", {}).get("/api/v1/approvals", {})
        self.assertIn("get", approvals_path)
        get_meta = approvals_path["get"]
        self.assertEqual(get_meta.get("summary"), "List governance approval requests")
        self.assertEqual(get_meta.get("operationId"), "list_approvals_api_v1_approvals_get")
        parameters = [p["name"] for p in get_meta.get("parameters", [])]
        self.assertIn("status", parameters)
        self.assertIn("trace_id", parameters)
        self.assertIn("policy_id", parameters)

    def test_authorization_decisions_compatibility_endpoint(self) -> None:
        """Verifies /api/v1/authorization/decisions acts as a working endpoint without returning 501."""
        auth_req = {
            "trace_id": "trc_decisions_compat",
            "subject": "service_agent",
            "action": "execute_tool",
            "resource": "resource_abc",
        }
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_compat_allow",
                name="Allow tool execution",
                priority=10,
                rules=[
                    RuleDefinition(
                        id="r_allow",
                        name="Allow tool",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="execute_tool",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )
        resp = self.client.post("/api/v1/authorization/decisions", json_data=auth_req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["policy_id"], "pol_compat_allow")

    def test_approvals_module_router_export(self) -> None:
        """Verifies backend.app.api.routes.approvals exports an APIRouter for backwards compatibility."""
        from fastapi import APIRouter
        from backend.app.api.routes.approvals import router as approvals_router

        self.assertIsInstance(approvals_router, APIRouter)


if __name__ == "__main__":
    unittest.main()


