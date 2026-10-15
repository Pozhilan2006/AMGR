"""Tests for AMGR Phase 4 Master Prompt 7: Policy Rollback, Reversion, and Restoration.

Validates:
- Monotonic revision creation upon rollback (e.g. v3 -> rollback to v1 creates v4 with v1 configuration).
- Both POST /policies/{id}/rollback and POST /policies/{id}/revert endpoints.
- Rejection of rollback targeting the already active revision (422 Unprocessable Entity).
- Rejection of rollback for nonexistent policy or nonexistent version (404 Not Found).
- Scope isolation (403 Forbidden when caller lacks scope claims).
- Human authority enforcement (403 Forbidden for AI/LLM actors).
- Approval invalidation: rolling back to a new revision invalidates prior pending approvals.
- Restoring retired/deleted policies from immutable history back into active definitions.
- Database persistence integrity across PolicyModel and PolicyVersionModel with transactional rollback.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.authorization import reset_services
from backend.app.api.routes.policies import (
    PolicyCallerIdentity,
    PolicyScope,
    reset_policy_service,
)
from backend.app.db.session import Base
from backend.app.main import create_app
from backend.app.models.policy import PolicyModel
from backend.app.models.policy_version import PolicyVersionModel
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import AuthorizationRequest
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyChangeType,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService
from backend.app.services.policy_service import PolicyService


class _TestResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = body

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class ASGIClient:
    def __init__(self, app_instance: Any, default_identity: Any = None) -> None:
        self.app = app_instance
        self.default_identity = default_identity

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        identity: Any = "__USE_DEFAULT__",
    ) -> _TestResponse:
        query_string = ""
        if "?" in path:
            path, qs = path.split("?", 1)
            query_string = qs

        if params:
            param_str = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            query_string = f"{query_string}&{param_str}" if query_string else param_str

        raw_body = b""
        if json_data is not None:
            raw_body = json.dumps(json_data).encode("utf-8")

        headers_list = [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw_body)).encode("utf-8")),
        ]

        active_identity = self.default_identity if identity == "__USE_DEFAULT__" else identity

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

    def get(self, path: str, params: dict[str, Any] | None = None, identity: Any = "__USE_DEFAULT__") -> _TestResponse:
        return self.request("GET", path, params=params, identity=identity)

    def post(self, path: str, json_data: Any = None, params: dict[str, Any] | None = None, identity: Any = "__USE_DEFAULT__") -> _TestResponse:
        return self.request("POST", path, params=params, json_data=json_data, identity=identity)

    def patch(self, path: str, json_data: Any = None, identity: Any = "__USE_DEFAULT__") -> _TestResponse:
        return self.request("PATCH", path, json_data=json_data, identity=identity)

    def delete(self, path: str, identity: Any = "__USE_DEFAULT__") -> _TestResponse:
        return self.request("DELETE", path, identity=identity)


class TestPolicyRollbackLifecycle(unittest.TestCase):
    """Test suite for policy rollback, reversion, and restoration functionality."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.decision_repo = DecisionRepository()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )
        self.policy_service = PolicyService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )

        self.human_identity = PolicyCallerIdentity(
            subject="security_admin_bob",
            authorized_scopes=[
                PolicyScope(application_id="billing-service", environment="production"),
                PolicyScope(application_id="payment-service", environment="production"),
            ],
            is_ai_or_llm=False,
        )

        self.ai_identity = PolicyCallerIdentity(
            subject="ai:automated-operator",
            authorized_scopes=[
                PolicyScope(application_id="billing-service", environment="production"),
            ],
            is_ai_or_llm=True,
        )

        self.unauthorized_scope_identity = PolicyCallerIdentity(
            subject="security_admin_charlie",
            authorized_scopes=[
                PolicyScope(application_id="other-service", environment="development"),
            ],
            is_ai_or_llm=False,
        )

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            policy_service=self.policy_service,
            decision_repo=self.decision_repo,
        )
        self.client = ASGIClient(self.app, default_identity=self.human_identity)

        # Baseline v1 policy targeting billing-service in production
        self.sample_policy_payload = {
            "id": "pol_data_guard",
            "name": "Data Guard V1",
            "description": "Baseline v1 data guard",
            "priority": 10,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_allow_billing",
                    "name": "Allow billing in prod",
                    "condition": {
                        "all_of": [
                            {
                                "field": "context.application_id",
                                "operator": "EQ",
                                "value": "billing-service",
                            },
                            {
                                "field": "context.environment",
                                "operator": "EQ",
                                "value": "production",
                            },
                        ]
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                    "enabled": True,
                }
            ],
        }

    def tearDown(self) -> None:
        reset_services(None, None, None, None)
        reset_policy_service(None)

    def test_rollback_active_policy_creates_new_monotonic_version(self) -> None:
        """Rolling back an active policy to v1 creates a new monotonic revision (e.g. v3) with v1 data."""
        # 1. Create v1
        resp_v1 = self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.assertEqual(resp_v1.status_code, 201)
        self.assertEqual(resp_v1.json()["version"], 1)
        self.assertEqual(resp_v1.json()["name"], "Data Guard V1")
        self.assertEqual(resp_v1.json()["priority"], 10)

        # 2. Update to v2
        resp_v2 = self.client.patch(
            "/api/v1/policies/pol_data_guard",
            json_data={"name": "Data Guard V2 Modified", "priority": 50},
        )
        self.assertEqual(resp_v2.status_code, 200)
        self.assertEqual(resp_v2.json()["version"], 2)
        self.assertEqual(resp_v2.json()["name"], "Data Guard V2 Modified")
        self.assertEqual(resp_v2.json()["priority"], 50)

        # 3. Rollback to v1 via POST /api/v1/policies/{id}/rollback
        resp_rb = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={
                "target_version": 1,
                "description": "Reverting erroneous v2 update back to stable v1",
            },
        )
        self.assertEqual(resp_rb.status_code, 200)
        rolled_back = resp_rb.json()

        # Monotonic revision increment
        self.assertEqual(rolled_back["version"], 3)
        # Content restored from v1
        self.assertEqual(rolled_back["name"], "Data Guard V1")
        self.assertEqual(rolled_back["priority"], 10)
        self.assertEqual(rolled_back["description"], "Baseline v1 data guard")
        self.assertEqual(len(rolled_back["rules"]), 1)
        self.assertEqual(rolled_back["rules"][0]["id"], "rule_allow_billing")

        # 4. Check revisions list (should contain v1, v2, v3 in ascending order)
        resp_revs = self.client.get("/api/v1/policies/pol_data_guard/revisions")
        self.assertEqual(resp_revs.status_code, 200)
        revisions = resp_revs.json()
        self.assertEqual([r["version"] for r in revisions], [1, 2, 3])
        self.assertEqual(revisions[0]["name"], "Data Guard V1")
        self.assertEqual(revisions[1]["name"], "Data Guard V2 Modified")
        self.assertEqual(revisions[2]["name"], "Data Guard V1")

        # 5. Check audit history (records ROLLED_BACK entry)
        resp_hist = self.client.get("/api/v1/policies/pol_data_guard/history")
        self.assertEqual(resp_hist.status_code, 200)
        history = resp_hist.json()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["change_type"], "CREATED")
        self.assertEqual(history[1]["change_type"], "UPDATED")
        self.assertEqual(history[2]["change_type"], "ROLLED_BACK")
        self.assertEqual(history[2]["version"], 3)
        self.assertEqual(history[2]["actor"], "security_admin_bob")
        self.assertIn("Reverting erroneous v2 update", history[2]["description"])

    def test_revert_alias_and_query_parameter_support(self) -> None:
        """Verifies POST /revert route alias and query parameter target_version support."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.client.patch(
            "/api/v1/policies/pol_data_guard",
            json_data={"name": "Data Guard V2"},
        )

        # Use /revert with query parameter
        resp = self.client.post("/api/v1/policies/pol_data_guard/revert?target_version=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["version"], 3)
        self.assertEqual(data["name"], "Data Guard V1")

    def test_rollback_to_same_active_version_rejected_422(self) -> None:
        """Attempting to rollback to the already active version is rejected with 422."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # Currently at v1; attempting to rollback to v1
        resp = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={"target_version": 1},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("already at revision 1", resp.json()["detail"].lower())

    def test_rollback_nonexistent_policy_or_version_returns_404(self) -> None:
        """Attempting to rollback a nonexistent policy or invalid version returns 404."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # 1. Nonexistent version
        resp_bad_ver = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={"target_version": 99},
        )
        self.assertEqual(resp_bad_ver.status_code, 404)
        self.assertIn("revision 99", resp_bad_ver.json()["detail"])

        # 2. Nonexistent policy
        resp_bad_pol = self.client.post(
            "/api/v1/policies/pol_missing/rollback",
            json_data={"target_version": 1},
        )
        self.assertEqual(resp_bad_pol.status_code, 404)

    def test_rollback_rejects_ai_operator_403(self) -> None:
        """Automated AI / LLM operators are strictly forbidden from executing rollbacks."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.client.patch("/api/v1/policies/pol_data_guard", json_data={"name": "V2"})

        resp = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={"target_version": 1},
            identity=self.ai_identity,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("automated ai/llm", resp.json()["detail"].lower())

    def test_rollback_scope_isolation_403(self) -> None:
        """Caller lacking authorization for policy scope is rejected with 403 Forbidden."""
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)
        self.client.patch("/api/v1/policies/pol_data_guard", json_data={"name": "V2"})

        resp = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={"target_version": 1},
            identity=self.unauthorized_scope_identity,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("outside caller's authorized scope", resp.json()["detail"].lower())

    def test_rollback_invalidates_pending_approvals_due_to_version_drift(self) -> None:
        """Rolling back a policy updates its version, invalidating prior pending approvals."""
        # 1. Create v1 with a REQUIRE_APPROVAL rule
        approval_policy_payload = {
            "id": "pol_critical_transfer",
            "name": "Critical Transfer Guard",
            "priority": 100,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_require_approval",
                    "name": "Require approval for large transfer",
                    "condition": {
                        "field": "context.application_id",
                        "operator": "EQ",
                        "value": "billing-service",
                    },
                    "effect": "REQUIRE_APPROVAL",
                    "priority": 100,
                    "enabled": True,
                }
            ],
        }
        self.client.post("/api/v1/policies", json_data=approval_policy_payload)

        # 2. Trigger runtime evaluation yielding REQUIRE_APPROVAL and pending ApprovalRequest
        auth_req = AuthorizationRequest(
            trace_id="trc_approval_drift_test",
            subject="operator_jane",
            action="execute_transfer",
            resource="db://billing-ledger",
            context={"application_id": "billing-service", "environment": "production"},
        )
        resp_auth = self.client.post("/api/v1/authorize", json_data=auth_req.model_dump(mode="json"))
        self.assertEqual(resp_auth.status_code, 200)
        self.assertEqual(resp_auth.json()["decision"], "REQUIRE_APPROVAL")
        approval_id = resp_auth.headers.get("x-amgr-approval-id")
        self.assertIsNotNone(approval_id)

        # Verify initial pending approval request
        appr_obj = self.approval_service.get_approval_request(approval_id)
        self.assertIsNotNone(appr_obj)
        self.assertEqual(appr_obj.status.value, "PENDING")
        self.assertEqual(appr_obj.policy_version, 1)

        # 3. Update policy to v2
        self.client.patch(
            "/api/v1/policies/pol_critical_transfer",
            json_data={"name": "Critical Transfer Guard V2"},
        )

        # 4. Rollback policy to v1 (creates v3)
        resp_rb = self.client.post(
            "/api/v1/policies/pol_critical_transfer/rollback",
            json_data={"target_version": 1},
        )
        self.assertEqual(resp_rb.status_code, 200)
        self.assertEqual(resp_rb.json()["version"], 3)

        # 5. Verify that prior approval request is now EXPIRED and can_proceed fails closed
        appr_after = self.approval_service.get_approval_request(approval_id)
        self.assertEqual(appr_after.status.value, "EXPIRED")
        self.assertFalse(self.approval_service.can_proceed(approval_id, policy_repo=self.policy_repo))

    def test_rollback_restores_deleted_policy_to_active_definitions(self) -> None:
        """Rolling back / restoring a deleted policy reactivates it as a new version and enforces rules."""
        # 1. Create v1
        self.client.post("/api/v1/policies", json_data=self.sample_policy_payload)

        # 2. Update to v2
        self.client.patch("/api/v1/policies/pol_data_guard", json_data={"name": "Data Guard V2"})

        # 3. Delete policy
        resp_del = self.client.delete("/api/v1/policies/pol_data_guard")
        self.assertEqual(resp_del.status_code, 200)

        # Policy is no longer in active listing
        resp_list = self.client.get("/api/v1/policies")
        self.assertEqual(len(resp_list.json()), 0)

        # 4. Rollback / restore to v1
        resp_restore = self.client.post(
            "/api/v1/policies/pol_data_guard/rollback",
            json_data={
                "target_version": 1,
                "description": "Restoring accidentally retired policy from v1",
            },
        )
        self.assertEqual(resp_restore.status_code, 200)
        restored = resp_restore.json()
        self.assertEqual(restored["version"], 3)
        self.assertEqual(restored["name"], "Data Guard V1")

        # 5. Policy is now active again in listings
        resp_list2 = self.client.get("/api/v1/policies")
        self.assertEqual(len(resp_list2.json()), 1)
        self.assertEqual(resp_list2.json()[0]["version"], 3)

        # 6. Runtime authorization evaluation succeeds against restored policy
        auth_req = AuthorizationRequest(
            trace_id="trc_restored_eval",
            subject="service_billing",
            action="execute_query",
            resource="db://billing-ledger",
            context={"application_id": "billing-service", "environment": "production"},
        )
        resp_eval = self.client.post("/api/v1/authorize", json_data=auth_req.model_dump(mode="json"))
        self.assertEqual(resp_eval.status_code, 200)
        self.assertEqual(resp_eval.json()["decision"], "ALLOW")
        self.assertEqual(resp_eval.json()["policy_id"], "pol_data_guard")

    def test_database_backed_rollback_persistence(self) -> None:
        """Verifies database persistence, atomic commit, and rollback error handling using SQLite."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        db: Session = session_factory()

        try:
            repo = PolicyRepository(db_session=db)
            service = PolicyService(policy_repo=repo)

            # Create policy in DB (v1)
            p1 = service.create_policy(
                PolicyCreate(
                    id="pol_db_guard",
                    name="DB Guard V1",
                    priority=20,
                    rules=[
                        RuleDefinition(
                            id="rule_1",
                            name="Rule 1",
                            condition=ConditionRule(
                                field="action",
                                operator=ConditionOperator.EQ,
                                value="read",
                            ),
                            effect=PolicyEffect.ALLOW,
                            priority=10,
                        )
                    ],
                ),
                actor="admin",
            )
            self.assertEqual(p1.version, 1)

            # Update to v2
            p2 = service.update_policy(
                "pol_db_guard",
                PolicyUpdate(name="DB Guard V2 Modified", priority=80),
                actor="admin",
            )
            self.assertEqual(p2.version, 2)

            # Verify PolicyModel in DB is at v2
            db_model = db.get(PolicyModel, "pol_db_guard")
            self.assertIsNotNone(db_model)
            self.assertEqual(db_model.version, 2)
            self.assertEqual(db_model.priority, 80)

            # Rollback to v1
            p3 = service.rollback_policy(
                "pol_db_guard",
                target_version=1,
                actor="auditor",
                description="Revert to v1 in DB",
            )
            self.assertEqual(p3.version, 3)
            self.assertEqual(p3.priority, 20)
            self.assertEqual(p3.name, "DB Guard V1")

            # Verify PolicyModel in DB is now updated to v3
            db.expire_all()
            db_model_updated = db.get(PolicyModel, "pol_db_guard")
            self.assertIsNotNone(db_model_updated)
            self.assertEqual(db_model_updated.version, 3)
            self.assertEqual(db_model_updated.priority, 20)
            self.assertEqual(db_model_updated.name, "DB Guard V1")

            # Verify PolicyVersionModel records in DB
            versions = (
                db.query(PolicyVersionModel)
                .filter_by(policy_id="pol_db_guard")
                .order_by(PolicyVersionModel.version.asc())
                .all()
            )
            self.assertEqual(len(versions), 3)
            self.assertEqual(versions[0].version, 1)
            self.assertEqual(versions[0].change_type, "CREATED")
            self.assertEqual(versions[1].version, 2)
            self.assertEqual(versions[1].change_type, "UPDATED")
            self.assertEqual(versions[2].version, 3)
            self.assertEqual(versions[2].change_type, "ROLLED_BACK")
            self.assertEqual(versions[2].actor, "auditor")
            self.assertEqual(versions[2].description, "Revert to v1 in DB")

        finally:
            db.close()
            Base.metadata.drop_all(engine)
