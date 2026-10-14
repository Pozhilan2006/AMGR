"""Unit and integration tests for AMGR Policy Lifecycle and CRUD API.

Tests authenticated policy CRUD endpoints:
- Caller authentication (401 on missing/invalid credentials)
- Scope isolation for application and environment (403 on unauthorized scope)
- AI/LLM mutation prevention (403 for automated entities on write operations)
- Creating policies (201 Created, 409 Conflict on duplicate)
- Retrieving policies by ID and version (200 OK, 404 Not Found)
- Updating policy metadata and conditions (200 OK, version incremented)
- Enabling and disabling policies (200 OK, approval invalidation)
- Deleting and retiring policies (200 OK, approval expiration, history retention)
- Rejection of arbitrary Python expressions, SQL fragments, and invalid operators (422)
"""

import asyncio
import json
import unittest
from typing import Any

from backend.app.api.routes.policies import (
    PolicyCallerIdentity,
    PolicyScope,
    get_authenticated_identity,
    get_policy_service,
    reset_policy_service,
)
from backend.app.main import create_app
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import ApprovalStatus
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import (
    PYDANTIC_AVAILABLE,
    ConditionGroup,
    ConditionRule,
)
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.policy_service import PolicyService


_DEFAULT_IDENTITY = object()


class _TestResponse:
    """Helper response wrapper for ASGI test executions."""

    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class ASGIClient:
    """Lightweight ASGI test client avoiding external requests/httpx dependencies."""

    def __init__(self, app: Any, default_identity: PolicyCallerIdentity | None = None) -> None:
        self.app = app
        self.default_identity = default_identity

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        identity: Any = _DEFAULT_IDENTITY,
    ) -> _TestResponse:
        query_string = ""
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)

        raw_body = b""
        if json_data is not None:
            raw_body = json.dumps(json_data).encode("utf-8")

        headers_list = [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw_body)).encode("utf-8")),
        ]

        active_identity = self.default_identity if identity is _DEFAULT_IDENTITY else identity

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string.encode("utf-8"),
            "headers": headers_list,
            "state": {},
        }

        if active_identity is not None:
            scope["state"]["identity"] = active_identity

        resp_headers: dict[str, str] = {}
        resp_body: list[bytes] = []
        status_code = 500

        async def receive() -> dict[str, Any]:
            return {
                "type": "http.request",
                "body": raw_body,
                "more_body": False,
            }

        async def send(message: dict[str, Any]) -> None:
            nonlocal status_code, resp_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                for k, v in message.get("headers", []):
                    resp_headers[k.decode("utf-8").lower()] = v.decode("utf-8")
            elif message["type"] == "http.response.body":
                resp_body.append(message.get("body", b""))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.app(scope, receive, send))
        finally:
            loop.close()

        return _TestResponse(status_code, resp_headers, b"".join(resp_body))

    def get(
        self, path: str, params: dict[str, Any] | None = None, identity: Any = _DEFAULT_IDENTITY
    ) -> _TestResponse:
        return self.request("GET", path, params=params, identity=identity)

    def post(
        self,
        path: str,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
        identity: Any = _DEFAULT_IDENTITY,
    ) -> _TestResponse:
        return self.request("POST", path, params=params, json_data=json_data, identity=identity)

    def put(
        self,
        path: str,
        json_data: Any = None,
        identity: Any = _DEFAULT_IDENTITY,
    ) -> _TestResponse:
        return self.request("PUT", path, json_data=json_data, identity=identity)

    def patch(
        self,
        path: str,
        json_data: Any = None,
        identity: Any = _DEFAULT_IDENTITY,
    ) -> _TestResponse:
        return self.request("PATCH", path, json_data=json_data, identity=identity)

    def delete(
        self, path: str, identity: Any = _DEFAULT_IDENTITY
    ) -> _TestResponse:
        return self.request("DELETE", path, identity=identity)


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyLifecycleCRUD(unittest.TestCase):
    """Exhaustive test suite for policy lifecycle foundation and CRUD implementation."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.policy_service = PolicyService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        reset_policy_service(self.policy_service)

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            policy_service=self.policy_service,
        )

        self.human_identity = PolicyCallerIdentity(
            subject="sec_engineer_alice",
            authorized_scopes=[
                PolicyScope(application_id="billing-agent", environment="production"),
                PolicyScope(application_id="billing-agent", environment="staging"),
            ],
            is_ai_or_llm=False,
        )

        self.ai_identity = PolicyCallerIdentity(
            subject="ai:security_copilot",
            authorized_scopes=[
                PolicyScope(application_id="billing-agent", environment="production"),
            ],
            is_ai_or_llm=True,
        )

        self.client = ASGIClient(self.app, default_identity=self.human_identity)

        # Reusable sample rule and policy payload
        self.sample_rule = {
            "id": "rule_block_export",
            "name": "Block Bulk Exports",
            "description": "Requires approval on export",
            "condition": {
                "field": "action",
                "operator": "EQ",
                "value": "export_data",
            },
            "effect": "REQUIRE_APPROVAL",
            "priority": 10,
            "enabled": True,
        }

        self.sample_policy_payload = {
            "id": "pol_billing_export_guard",
            "name": "Billing Export Guard",
            "description": "Monitors customer exports",
            "priority": 50,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [self.sample_rule],
        }

    def tearDown(self) -> None:
        reset_policy_service(None)

    # 1. Authentication tests
    def test_unauthenticated_request_rejected_with_401(self) -> None:
        """Requests without caller identity must return HTTP 401 Unauthorized."""
        resp = self.client.get("/api/v1/policies", identity=None)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Authentication is required", resp.json()["detail"])

        resp_create = self.client.post("/api/v1/policies", json_data=self.sample_policy_payload, identity=None)
        self.assertEqual(resp_create.status_code, 401)

    # 2. AI / LLM entity mutation prevention
    def test_ai_caller_cannot_mutate_policies_403(self) -> None:
        """Automated AI/LLM entities are forbidden from creating, updating, or deleting policies."""
        # AI caller cannot create
        resp_create = self.client.post(
            "/api/v1/policies",
            json_data=self.sample_policy_payload,
            identity=self.ai_identity,
        )
        self.assertEqual(resp_create.status_code, 403)
        self.assertIn("AI/LLM entities are forbidden", resp_create.json()["detail"])

        # Create as human first
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # AI caller cannot update
        resp_update = self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data={"name": "Tampered By AI"},
            identity=self.ai_identity,
        )
        self.assertEqual(resp_update.status_code, 403)

        # AI caller cannot enable/disable
        resp_disable = self.client.post(
            "/api/v1/policies/pol_billing_export_guard/disable",
            identity=self.ai_identity,
        )
        self.assertEqual(resp_disable.status_code, 403)

        # AI caller cannot delete
        resp_del = self.client.delete(
            "/api/v1/policies/pol_billing_export_guard",
            identity=self.ai_identity,
        )
        self.assertEqual(resp_del.status_code, 403)

    def test_ai_caller_can_read_policies_within_authorized_scope(self) -> None:
        """AI entities may read policies within their authorized scope to formulate proposals."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        resp = self.client.get("/api/v1/policies", identity=self.ai_identity)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "pol_billing_export_guard")

    # 3. Scope isolation tests
    def test_requesting_unauthorized_scope_returns_403(self) -> None:
        """Specifying an application or environment outside authorized scopes returns HTTP 403."""
        resp = self.client.get(
            "/api/v1/policies",
            params={"application_id": "payroll-agent", "environment": "production"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("not authorized", resp.json()["detail"])

    def test_policy_scope_isolation_on_listing(self) -> None:
        """Policies targeting a different application are excluded from caller's scope view."""
        # Policy 1: Global (no specific app constraint)
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # Policy 2: Strictly scoped to payroll-agent via condition
        payroll_rule = {
            "id": "rule_payroll_only",
            "name": "Payroll Check",
            "condition": {
                "field": "context.application_id",
                "operator": "EQ",
                "value": "payroll-agent",
            },
            "effect": "BLOCK",
            "priority": 1,
            "enabled": True,
        }
        payroll_policy = {
            "id": "pol_payroll_guard",
            "name": "Payroll Guard",
            "priority": 10,
            "rules": [payroll_rule],
        }

        # Create directly via repo to simulate existing store with multiple app policies
        self.policy_repo.create_policy(PolicyCreate.model_validate(payroll_policy))

        # Human caller authorized only for billing-agent
        resp = self.client.get("/api/v1/policies", params={"application_id": "billing-agent"})
        self.assertEqual(resp.status_code, 200)
        pols = resp.json()
        pol_ids = [p["id"] for p in pols]
        self.assertIn("pol_billing_export_guard", pol_ids)
        self.assertNotIn("pol_payroll_guard", pol_ids)

    def test_get_policy_outside_authorized_scope_returns_403(self) -> None:
        """Retrieving an individual policy that strictly belongs to another scope is rejected."""
        payroll_policy = {
            "id": "pol_payroll_isolated",
            "name": "Payroll Isolated Policy",
            "priority": 10,
            "rules": [
                {
                    "id": "rule_payroll_cond",
                    "name": "Payroll Only",
                    "condition": {
                        "field": "context.application_id",
                        "operator": "EQ",
                        "value": "payroll-agent",
                    },
                    "effect": "BLOCK",
                    "priority": 1,
                    "enabled": True,
                }
            ],
        }
        self.policy_repo.create_policy(PolicyCreate.model_validate(payroll_policy))

        resp = self.client.get("/api/v1/policies/pol_payroll_isolated")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("outside caller's authorized", resp.json()["detail"])

    def test_create_policy_targeting_unauthorized_scope_returns_403(self) -> None:
        """Creating a policy whose rules target an unauthorized application is rejected."""
        bad_policy = dict(self.sample_policy_payload)
        bad_policy["id"] = "pol_unauthorized_scope"
        bad_policy["rules"] = [
            {
                "id": "rule_target_hr",
                "name": "HR Target",
                "condition": {
                    "field": "context.application_id",
                    "operator": "EQ",
                    "value": "hr-workday-agent",
                },
                "effect": "BLOCK",
                "priority": 1,
                "enabled": True,
            }
        ]
        resp = self.client.post("/api/v1/policies", json_data=bad_policy)
        self.assertEqual(resp.status_code, 403)

    # 4. CRUD operations tests
    def test_create_and_retrieve_policy_lifecycle(self) -> None:
        """Policy creation establishes v1 and makes it retrievable by stable ID."""
        resp_create = self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.assertEqual(resp_create.status_code, 201)
        created = resp_create.json()
        self.assertEqual(created["id"], "pol_billing_export_guard")
        self.assertEqual(created["version"], 1)
        self.assertTrue(created["enabled"])
        self.assertIsNotNone(created["created_at"])

        # Retrieve by stable ID
        resp_get = self.client.get("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(resp_get.json()["id"], "pol_billing_export_guard")
        self.assertEqual(resp_get.json()["version"], 1)

        # Retrieve with version query param
        resp_get_v1 = self.client.get("/api/v1/policies/pol_billing_export_guard", params={"version": 1})
        self.assertEqual(resp_get_v1.status_code, 200)
        self.assertEqual(resp_get_v1.json()["version"], 1)

    def test_create_duplicate_policy_id_returns_409_conflict(self) -> None:
        """Attempting to create a policy with an ID that already exists returns HTTP 409 Conflict."""
        resp1 = self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.assertEqual(resp1.status_code, 201)

        resp2 = self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.assertEqual(resp2.status_code, 409)
        self.assertIn("already exists", resp2.json()["detail"])

    def test_get_nonexistent_policy_returns_404(self) -> None:
        """Retrieving a non-existent policy ID returns HTTP 404 Not Found."""
        resp = self.client.get("/api/v1/policies/pol_non_existent")
        self.assertEqual(resp.status_code, 404)

    def test_update_policy_creates_new_revision_and_retains_v1(self) -> None:
        """Updating a policy creates monotonic revision v2 while retaining v1 in history."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        update_payload = {
            "name": "Updated Billing Guard",
            "priority": 99,
            "description": "Updated description for revision 2",
        }
        resp_patch = self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data=update_payload,
        )
        self.assertEqual(resp_patch.status_code, 200)
        updated = resp_patch.json()
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["name"], "Updated Billing Guard")
        self.assertEqual(updated["priority"], 99)

        # Current version returns v2
        resp_current = self.client.get("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_current.json()["version"], 2)

        # Historical query returns v1 unchanged
        resp_v1 = self.client.get("/api/v1/policies/pol_billing_export_guard", params={"version": 1})
        self.assertEqual(resp_v1.json()["version"], 1)
        self.assertEqual(resp_v1.json()["name"], "Billing Export Guard")

    def test_enable_and_disable_policy_endpoints(self) -> None:
        """Enabling and disabling policies updates state monotonically and invalidates pending approvals."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # Create a pending approval for this policy
        req_auth = AuthorizationRequest(
            trace_id="trc_test_approval",
            subject="agent_user",
            action="export_data",
            resource="db://billing",
        )
        dec_auth = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_billing_export_guard",
            matched_rule_id="rule_block_export",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Export requires approval",
        )
        appr_req = self.approval_service.create_approval_request(
            authorization_decision=dec_auth,
            authorization_request=req_auth,
            policy_version=1,
        )
        self.assertEqual(appr_req.status, ApprovalStatus.PENDING)

        # Disable policy
        resp_dis = self.client.post("/api/v1/policies/pol_billing_export_guard/disable")
        self.assertEqual(resp_dis.status_code, 200)
        self.assertFalse(resp_dis.json()["enabled"])
        self.assertEqual(resp_dis.json()["version"], 2)

        # Verify pending approval was automatically invalidated (EXPIRED)
        expired_appr = self.approval_service.get_approval_request(appr_req.id)
        self.assertIsNotNone(expired_appr)
        self.assertEqual(expired_appr.status, ApprovalStatus.EXPIRED)
        self.assertEqual(expired_appr.final_effect, PolicyEffect.BLOCK)

        # Re-enable policy
        resp_en = self.client.post("/api/v1/policies/pol_billing_export_guard/enable")
        self.assertEqual(resp_en.status_code, 200)
        self.assertTrue(resp_en.json()["enabled"])
        self.assertEqual(resp_en.json()["version"], 3)

    def test_delete_policy_retires_policy_and_expires_approvals(self) -> None:
        """Deleting/retiring a policy removes active definition, expires approvals, and retains history."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # Add a pending approval
        req_auth = AuthorizationRequest(
            trace_id="trc_to_delete",
            subject="agent_worker",
            action="export_data",
            resource="db://billing",
        )
        dec_auth = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id="pol_billing_export_guard",
            matched_rule_id="rule_block_export",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Approval pending",
        )
        appr_req = self.approval_service.create_approval_request(
            authorization_decision=dec_auth,
            authorization_request=req_auth,
            policy_version=1,
        )

        # Delete policy
        resp_del = self.client.delete("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_del.status_code, 200)
        self.assertEqual(resp_del.json()["status"], "deleted")

        # GET current returns 404
        resp_get = self.client.get("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_get.status_code, 404)

        # Historical revisions still preserved in repository
        history = self.policy_repo.get_change_history("pol_billing_export_guard")
        self.assertTrue(len(history) >= 2)  # CREATED and DEACTIVATED/DELETED

        # Approval request was invalidated
        appr = self.approval_service.get_approval_request(appr_req.id)
        self.assertEqual(appr.status, ApprovalStatus.EXPIRED)
        self.assertEqual(appr.final_effect, PolicyEffect.BLOCK)

        # can_proceed fails closed
        self.assertFalse(self.approval_service.can_proceed(appr_req.id, policy_repo=self.policy_repo))

    # 5. Security & Injection validation tests
    def test_reject_python_code_injection_in_condition(self) -> None:
        """Arbitrary Python execution attempts in condition values are rejected with HTTP 422."""
        malicious_payload = dict(self.sample_policy_payload)
        malicious_payload["rules"] = [
            {
                "id": "rule_exploit",
                "name": "Exploit Rule",
                "condition": {
                    "field": "action",
                    "operator": "EQ",
                    "value": "__import__('os').system('touch /tmp/pwned')",
                },
                "effect": "BLOCK",
            }
        ]
        resp = self.client.post("/api/v1/policies", json_data=malicious_payload)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("Arbitrary executable expression", resp.json()["detail"])

    def test_reject_eval_exec_in_condition(self) -> None:
        """eval/exec calls in conditions are rejected with HTTP 422."""
        malicious_payload = dict(self.sample_policy_payload)
        malicious_payload["rules"] = [
            {
                "id": "rule_eval",
                "name": "Eval Rule",
                "condition": {
                    "field": "action",
                    "operator": "EQ",
                    "value": "eval('2 + 2')",
                },
                "effect": "BLOCK",
            }
        ]
        resp = self.client.post("/api/v1/policies", json_data=malicious_payload)
        self.assertEqual(resp.status_code, 422)

    def test_reject_sql_fragment_injection(self) -> None:
        """SQL injection fragments in conditions are rejected with HTTP 422."""
        sql_injections = [
            "'; DROP TABLE policies;--",
            "val' UNION SELECT * FROM users--",
            "1' OR '1'='1",
        ]
        for sql in sql_injections:
            with self.subTest(sql=sql):
                payload = dict(self.sample_policy_payload)
                payload["rules"] = [
                    {
                        "id": "rule_sql",
                        "name": "SQL Injection Rule",
                        "condition": {
                            "field": "action",
                            "operator": "EQ",
                            "value": sql,
                        },
                        "effect": "BLOCK",
                    }
                ]
                resp = self.client.post("/api/v1/policies", json_data=payload)
                self.assertEqual(resp.status_code, 422)
                self.assertIn("SQL fragment or injection pattern", resp.json()["detail"])

    def test_reject_unsupported_condition_operator(self) -> None:
        """Unsupported condition operators are rejected with HTTP 422."""
        bad_operator_payload = dict(self.sample_policy_payload)
        bad_operator_payload["rules"] = [
            {
                "id": "rule_bad_op",
                "name": "Bad Operator Rule",
                "condition": {
                    "field": "action",
                    "operator": "REGEX_MATCH",  # Unsupported operator
                    "value": ".*",
                },
                "effect": "BLOCK",
            }
        ]
        resp = self.client.post("/api/v1/policies", json_data=bad_operator_payload)
        self.assertEqual(resp.status_code, 422)

    # 6. Pagination & Error Handling tests
    def test_list_policies_pagination_limit_and_offset(self) -> None:
        """Querying policies with limit and offset applies pagination correctly."""
        for i in range(5):
            p = dict(self.sample_policy_payload)
            p["id"] = f"pol_pagination_{i}"
            p["priority"] = 100 - i
            p["rules"] = [
                {
                    "id": f"r_pag_{i}",
                    "name": f"Rule {i}",
                    "condition": {"field": "action", "operator": "EQ", "value": f"act_{i}"},
                    "effect": "ALLOW",
                }
            ]
            self.client.post("/api/v1/policies", json_data=p)

        # Page 1: limit 2, offset 0 -> 2 items
        resp_p1 = self.client.get("/api/v1/policies", params={"limit": 2, "offset": 0})
        self.assertEqual(resp_p1.status_code, 200)
        p1_data = resp_p1.json()
        self.assertEqual(len(p1_data), 2)
        self.assertEqual(p1_data[0]["id"], "pol_pagination_0")
        self.assertEqual(p1_data[1]["id"], "pol_pagination_1")

        # Page 2: limit 2, offset 2 -> next 2 items
        resp_p2 = self.client.get("/api/v1/policies", params={"limit": 2, "offset": 2})
        self.assertEqual(resp_p2.status_code, 200)
        p2_data = resp_p2.json()
        self.assertEqual(len(p2_data), 2)
        self.assertEqual(p2_data[0]["id"], "pol_pagination_2")
        self.assertEqual(p2_data[1]["id"], "pol_pagination_3")

    def test_get_missing_policy_version_returns_informative_404(self) -> None:
        """Retrieving a non-existent version of an existing policy returns an informative 404."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        resp = self.client.get("/api/v1/policies/pol_billing_export_guard", params={"version": 999})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("revision 999 not found", resp.json()["detail"])

    # 7. Database persistence and transactional failure tests
    def test_persistence_failure_rollback_and_500_response(self) -> None:
        """Database commit failure rolls back transaction, preserves in-memory state, and returns HTTP 500."""
        from unittest.mock import MagicMock
        from backend.app.repositories.policy_repository import PolicyPersistenceError

        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("Disk I/O failure")
        failing_repo = PolicyRepository(db_session=mock_db)
        failing_service = PolicyService(policy_repo=failing_repo, approval_service=self.approval_service)
        failing_app = create_app(
            policy_repo=failing_repo,
            approval_service=self.approval_service,
            policy_service=failing_service,
        )
        client = ASGIClient(failing_app, default_identity=self.human_identity)

        resp = client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Database persistence failure", resp.json()["detail"])

        # Rollback was called on db session
        mock_db.rollback.assert_called_once()

        # In-memory repository state was NOT polluted
        self.assertIsNone(failing_repo.get_policy("pol_billing_export_guard"))

    def test_database_backed_policy_lifecycle_end_to_end(self) -> None:
        """Relational database backed repository persists policies, revisions, and deletions cleanly."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlalchemy.orm import sessionmaker
        from backend.app.db.session import Base
        from backend.app.models.policy import PolicyModel
        from backend.app.models.policy_version import PolicyVersionModel

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()

        try:
            db_repo = PolicyRepository(db_session=db)
            db_service = PolicyService(policy_repo=db_repo, approval_service=self.approval_service)
            db_app = create_app(
                policy_repo=db_repo,
                approval_service=self.approval_service,
                policy_service=db_service,
            )
            client = ASGIClient(db_app, default_identity=self.human_identity)

            # Create policy via API
            resp_create = client.post("/api/v1/policies", json_data=self.sample_policy_payload)
            self.assertEqual(resp_create.status_code, 201)

            # Verify PolicyModel and PolicyVersionModel in database
            db_pol = db.get(PolicyModel, "pol_billing_export_guard")
            self.assertIsNotNone(db_pol)
            self.assertEqual(db_pol.name, "Billing Export Guard")
            self.assertEqual(db_pol.version, 1)

            # Update policy via API
            resp_update = client.patch(
                "/api/v1/policies/pol_billing_export_guard",
                json_data={"name": "SQL Backed Title"},
            )
            self.assertEqual(resp_update.status_code, 200)

            # Verify version 2 in DB
            db.refresh(db_pol)
            self.assertEqual(db_pol.name, "SQL Backed Title")
            self.assertEqual(db_pol.version, 2)

            # Delete policy via API
            resp_del = client.delete("/api/v1/policies/pol_billing_export_guard")
            self.assertEqual(resp_del.status_code, 200)

            # PolicyModel is removed from active definitions in DB
            db_deleted = db.get(PolicyModel, "pol_billing_export_guard")
            self.assertIsNone(db_deleted)

        finally:
            db.close()
            Base.metadata.drop_all(engine)

    # 8. Master Prompt 3: Versioning, Revision History, and Audit Integrity tests
    def test_list_policy_revisions_endpoint_ordering_and_content(self) -> None:
        """GET /{policy_id}/revisions returns all immutable historical revisions in ascending version order."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # Update to v2
        self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data={"name": "V2 Billing Guard", "priority": 60},
        )
        # Update to v3
        self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data={"name": "V3 Billing Guard", "priority": 70},
        )

        resp = self.client.get("/api/v1/policies/pol_billing_export_guard/revisions")
        self.assertEqual(resp.status_code, 200)
        revisions = resp.json()
        self.assertEqual(len(revisions), 3)

        # Ascending version ordering
        self.assertEqual([r["version"] for r in revisions], [1, 2, 3])
        self.assertEqual(revisions[0]["name"], "Billing Export Guard")
        self.assertEqual(revisions[0]["priority"], 50)
        self.assertEqual(revisions[1]["name"], "V2 Billing Guard")
        self.assertEqual(revisions[1]["priority"], 60)
        self.assertEqual(revisions[2]["name"], "V3 Billing Guard")
        self.assertEqual(revisions[2]["priority"], 70)

    def test_list_policy_history_endpoint_ordering_and_events(self) -> None:
        """GET /{policy_id}/history returns audit trail entries in consistent chronological order."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data={"name": "Audit Updated"},
        )
        self.client.post("/api/v1/policies/pol_billing_export_guard/disable")
        self.client.post("/api/v1/policies/pol_billing_export_guard/enable")

        resp = self.client.get("/api/v1/policies/pol_billing_export_guard/history")
        self.assertEqual(resp.status_code, 200)
        history = resp.json()
        self.assertEqual(len(history), 4)

        # Verify ordering and change classifications
        self.assertEqual([h["version"] for h in history], [1, 2, 3, 4])
        self.assertEqual(history[0]["change_type"], "CREATED")
        self.assertEqual(history[1]["change_type"], "UPDATED")
        self.assertEqual(history[2]["change_type"], "DEACTIVATED")
        self.assertEqual(history[3]["change_type"], "ACTIVATED")

        # Every entry has timestamp and actor attribution
        for entry in history:
            self.assertEqual(entry["policy_id"], "pol_billing_export_guard")
            self.assertEqual(entry["actor"], "sec_engineer_alice")
            self.assertIsNotNone(entry["timestamp"])

    def test_historical_revisions_and_history_retrievable_after_deletion(self) -> None:
        """Retiring/deleting a policy does not erase historical revisions or audit history."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.client.patch(
            "/api/v1/policies/pol_billing_export_guard",
            json_data={"name": "Final Active Version", "priority": 85},
        )

        # Delete policy
        resp_del = self.client.delete("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_del.status_code, 200)

        # 1. Current policy retrieval returns 404 (retired from active definitions)
        resp_current = self.client.get("/api/v1/policies/pol_billing_export_guard")
        self.assertEqual(resp_current.status_code, 404)

        # 2. Historical revisions remain retrievable by version query
        resp_v1 = self.client.get("/api/v1/policies/pol_billing_export_guard", params={"version": 1})
        self.assertEqual(resp_v1.status_code, 200)
        self.assertEqual(resp_v1.json()["version"], 1)
        self.assertEqual(resp_v1.json()["name"], "Billing Export Guard")

        resp_v2 = self.client.get("/api/v1/policies/pol_billing_export_guard", params={"version": 2})
        self.assertEqual(resp_v2.status_code, 200)
        self.assertEqual(resp_v2.json()["version"], 2)
        self.assertEqual(resp_v2.json()["name"], "Final Active Version")

        # 3. All revisions remain retrievable via /revisions endpoint
        resp_revs = self.client.get("/api/v1/policies/pol_billing_export_guard/revisions")
        self.assertEqual(resp_revs.status_code, 200)
        self.assertEqual([r["version"] for r in resp_revs.json()], [1, 2])

        # 4. History endpoint retains complete audit trail including DEACTIVATED deletion entry
        resp_hist = self.client.get("/api/v1/policies/pol_billing_export_guard/history")
        self.assertEqual(resp_hist.status_code, 200)
        history = resp_hist.json()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["change_type"], "CREATED")
        self.assertEqual(history[1]["change_type"], "UPDATED")
        self.assertEqual(history[2]["change_type"], "DEACTIVATED")
        self.assertIn("retired", history[2]["description"].lower())

    def test_revisions_and_history_scope_isolation_and_ai_read_access(self) -> None:
        """Scope isolation and auth checks apply to revisions and history endpoints; AI callers can read."""
        scoped_policy = dict(self.sample_policy_payload)
        scoped_policy["id"] = "pol_scoped_billing_only"
        scoped_policy["rules"] = [
            {
                "id": "rule_scoped_billing",
                "name": "Billing Scope Rule",
                "condition": {
                    "field": "context.application_id",
                    "operator": "EQ",
                    "value": "billing-agent",
                },
                "effect": "BLOCK",
            }
        ]
        self.client.post("/api/v1/policies", json_data=scoped_policy)

        # 1. Unauthenticated request rejected with 401
        anon_client = ASGIClient(self.app, default_identity=None)
        resp_anon_rev = anon_client.get("/api/v1/policies/pol_scoped_billing_only/revisions")
        self.assertEqual(resp_anon_rev.status_code, 401)
        resp_anon_hist = anon_client.get("/api/v1/policies/pol_scoped_billing_only/history")
        self.assertEqual(resp_anon_hist.status_code, 401)

        # 2. Scope-restricted caller outside policy scope rejected with 403
        unauthorized_identity = PolicyCallerIdentity(
            subject="operator_hr",
            authorized_scopes=[
                PolicyScope(application_id="hr-workday-agent", environment="production")
            ],
            is_ai_or_llm=False,
        )
        unauth_client = ASGIClient(self.app, default_identity=unauthorized_identity)
        resp_unauth_rev = unauth_client.get("/api/v1/policies/pol_scoped_billing_only/revisions")
        self.assertEqual(resp_unauth_rev.status_code, 403)
        resp_unauth_hist = unauth_client.get("/api/v1/policies/pol_scoped_billing_only/history")
        self.assertEqual(resp_unauth_hist.status_code, 403)

        # 3. AI caller within authorized scope can read revisions and history
        ai_client = ASGIClient(self.app, default_identity=self.ai_identity)
        resp_ai_rev = ai_client.get("/api/v1/policies/pol_scoped_billing_only/revisions")
        self.assertEqual(resp_ai_rev.status_code, 200)
        self.assertEqual(len(resp_ai_rev.json()), 1)

        resp_ai_hist = ai_client.get("/api/v1/policies/pol_scoped_billing_only/history")
        self.assertEqual(resp_ai_hist.status_code, 200)
        self.assertEqual(len(resp_ai_hist.json()), 1)

        # 4. Nonexistent policy returns 404
        resp_404_rev = self.client.get("/api/v1/policies/pol_does_not_exist/revisions")
        self.assertEqual(resp_404_rev.status_code, 404)
        resp_404_hist = self.client.get("/api/v1/policies/pol_does_not_exist/history")
        self.assertEqual(resp_404_hist.status_code, 404)


if __name__ == "__main__":
    unittest.main()

