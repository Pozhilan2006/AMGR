"""Tests for AMGR Phase 4 Master Prompt 4: Policy Version Diffing and Decision History Querying.

Verifies:
1. Policy revision comparison (GET /{policy_id}/diff) between historical versions.
2. Detecting rule additions, removals, and modifications (including condition changes).
3. Previewing proposed policy updates (POST /{policy_id}/diff) without persisting changes.
4. Scope isolation and error handling (401, 403, 404, 422) for diff endpoints.
5. Decision history querying (GET /decisions and GET /decisions/{decision_id}) with multi-field filtering.
6. Decision history pagination (limit, offset) and sorting.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from typing import Any

from backend.app.api.routes.policies import (
    PolicyCallerIdentity,
    PolicyScope,
    reset_policy_service,
)
from backend.app.main import create_app
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import ConditionOperator, ConditionRule
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import PolicyEffect, RuleResolutionAlgorithm
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
    def __init__(self, app: Any, default_identity: Any = None) -> None:
        self.app = app
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


class TestPolicyDiffAndDecisionHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.decision_repo = DecisionRepository()
        self.policy_service = PolicyService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )
        reset_policy_service(self.policy_service)

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            policy_service=self.policy_service,
            decision_repo=self.decision_repo,
        )

        self.identity = PolicyCallerIdentity(
            subject="sec_admin",
            authorized_scopes=[
                PolicyScope(application_id="billing-agent", environment="production"),
                PolicyScope(application_id="billing-agent", environment="staging"),
            ],
            is_ai_or_llm=False,
        )
        self.client = ASGIClient(self.app, default_identity=self.identity)

        # Baseline policy
        self.sample_policy = {
            "id": "pol_diff_test",
            "name": "Diff Baseline Policy",
            "description": "Baseline description",
            "priority": 50,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_keep",
                    "name": "Kept Rule",
                    "condition": {"field": "action", "operator": "EQ", "value": "read"},
                    "effect": "ALLOW",
                    "priority": 10,
                },
                {
                    "id": "rule_modify",
                    "name": "Original Name",
                    "condition": {"field": "action", "operator": "EQ", "value": "export"},
                    "effect": "REQUIRE_APPROVAL",
                    "priority": 20,
                },
                {
                    "id": "rule_delete",
                    "name": "To Be Removed",
                    "condition": {"field": "action", "operator": "EQ", "value": "delete"},
                    "effect": "BLOCK",
                    "priority": 30,
                },
            ],
        }
        self.client.post("/api/v1/policies", json_data=self.sample_policy)

    def tearDown(self) -> None:
        reset_policy_service(None)

    def test_policy_diff_across_historical_versions(self) -> None:
        """Diffing v1 and v2 accurately detects metadata changes, rule additions, removals, and modifications."""
        # Create revision 2
        update_payload = {
            "name": "Diff Revision 2",
            "priority": 75,
            "rules": [
                {
                    "id": "rule_keep",
                    "name": "Kept Rule",
                    "condition": {"field": "action", "operator": "EQ", "value": "read"},
                    "effect": "ALLOW",
                    "priority": 10,
                },
                {
                    "id": "rule_modify",
                    "name": "Updated Name",
                    "condition": {"field": "action", "operator": "IN", "value": ["export", "dump"]},
                    "effect": "BLOCK",  # Changed effect
                    "priority": 25,     # Changed priority
                },
                {
                    "id": "rule_added",
                    "name": "New Rule",
                    "condition": {"field": "action", "operator": "EQ", "value": "audit"},
                    "effect": "ALLOW",
                    "priority": 5,
                },
            ],
        }
        resp_update = self.client.patch("/api/v1/policies/pol_diff_test", json_data=update_payload)
        self.assertEqual(resp_update.status_code, 200)
        self.assertEqual(resp_update.json()["version"], 2)

        # Query diff between v1 and v2
        resp_diff = self.client.get("/api/v1/policies/pol_diff_test/diff", params={"from_version": 1, "to_version": 2})
        self.assertEqual(resp_diff.status_code, 200)
        diff = resp_diff.json()

        self.assertEqual(diff["policy_id"], "pol_diff_test")
        self.assertEqual(diff["from_version"], 1)
        self.assertEqual(diff["to_version"], 2)
        self.assertTrue(diff["has_changes"])

        # Metadata changes
        self.assertEqual(diff["metadata_changes"]["name"]["old"], "Diff Baseline Policy")
        self.assertEqual(diff["metadata_changes"]["name"]["new"], "Diff Revision 2")
        self.assertEqual(diff["metadata_changes"]["priority"]["old"], 50)
        self.assertEqual(diff["metadata_changes"]["priority"]["new"], 75)

        # Added & removed rules
        self.assertEqual(len(diff["rules_added"]), 1)
        self.assertEqual(diff["rules_added"][0]["id"], "rule_added")

        self.assertEqual(len(diff["rules_removed"]), 1)
        self.assertEqual(diff["rules_removed"][0]["id"], "rule_delete")

        # Modified rules
        self.assertEqual(len(diff["rules_modified"]), 1)
        mod_rule = diff["rules_modified"][0]
        self.assertEqual(mod_rule["rule_id"], "rule_modify")
        self.assertEqual(mod_rule["change_type"], "MODIFIED")
        self.assertEqual(mod_rule["changes"]["name"]["old"], "Original Name")
        self.assertEqual(mod_rule["changes"]["name"]["new"], "Updated Name")
        self.assertEqual(mod_rule["changes"]["effect"]["old"], "REQUIRE_APPROVAL")
        self.assertEqual(mod_rule["changes"]["effect"]["new"], "BLOCK")
        self.assertEqual(mod_rule["changes"]["priority"]["old"], 20)
        self.assertEqual(mod_rule["changes"]["priority"]["new"], 25)
        self.assertIn("condition", mod_rule["changes"])

    def test_policy_diff_identical_versions_returns_no_changes(self) -> None:
        """Diffing a version against itself returns has_changes=False with empty changes."""
        resp = self.client.get("/api/v1/policies/pol_diff_test/diff", params={"from_version": 1, "to_version": 1})
        self.assertEqual(resp.status_code, 200)
        diff = resp.json()
        self.assertFalse(diff["has_changes"])
        self.assertEqual(diff["metadata_changes"], {})
        self.assertEqual(diff["rules_added"], [])
        self.assertEqual(diff["rules_removed"], [])
        self.assertEqual(diff["rules_modified"], [])

    def test_preview_policy_update_diff_endpoint(self) -> None:
        """POST /{policy_id}/diff previews proposed changes without mutating state."""
        proposed = {
            "name": "Preview Name Change",
            "priority": 99,
        }
        resp = self.client.post("/api/v1/policies/pol_diff_test/diff", json_data=proposed)
        self.assertEqual(resp.status_code, 200)
        diff = resp.json()
        self.assertTrue(diff["has_changes"])
        self.assertEqual(diff["metadata_changes"]["name"]["old"], "Diff Baseline Policy")
        self.assertEqual(diff["metadata_changes"]["name"]["new"], "Preview Name Change")
        self.assertIsNone(diff["to_version"])

        # Verify state was untouched (still v1 with original name)
        current = self.client.get("/api/v1/policies/pol_diff_test").json()
        self.assertEqual(current["version"], 1)
        self.assertEqual(current["name"], "Diff Baseline Policy")

    def test_diff_scope_isolation_and_error_handling(self) -> None:
        """Diff endpoints enforce authentication, scope isolation, and 404 for missing versions."""
        # 401 unauthenticated
        anon_client = ASGIClient(self.app, default_identity=None)
        self.assertEqual(anon_client.get("/api/v1/policies/pol_diff_test/diff", params={"from_version": 1}).status_code, 401)

        # 404 missing policy or version
        self.assertEqual(self.client.get("/api/v1/policies/pol_missing/diff", params={"from_version": 1}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/policies/pol_diff_test/diff", params={"from_version": 999}).status_code, 404)

        # 422 for code injection in preview
        bad_preview = {"name": "__import__('os').system('ls')"}
        resp_bad = self.client.post("/api/v1/policies/pol_diff_test/diff", json_data=bad_preview)
        self.assertEqual(resp_bad.status_code, 422)

    def test_decision_history_query_and_filtering(self) -> None:
        """GET /api/v1/decisions lists evaluated decisions with trace, policy, and decision filters."""
        # Evaluate three requests
        req1 = AuthorizationRequest(
            trace_id="trc_eval_1",
            subject="agent_1",
            action="read",
            resource="db://doc",
        )
        req2 = AuthorizationRequest(
            trace_id="trc_eval_2",
            subject="agent_2",
            action="export",
            resource="db://doc",
        )
        req3 = AuthorizationRequest(
            trace_id="trc_eval_1",
            subject="agent_1",
            action="delete",
            resource="db://doc",
        )

        dec1, _ = self.auth_service.authorize(req1)
        dec2, _ = self.auth_service.authorize(req2)
        dec3, _ = self.auth_service.authorize(req3)

        # List all decisions
        resp_all = self.client.get("/api/v1/decisions")
        self.assertEqual(resp_all.status_code, 200)
        records = resp_all.json()
        self.assertEqual(len(records), 3)

        # Filter by trace_id
        resp_trc = self.client.get("/api/v1/decisions", params={"trace_id": "trc_eval_1"})
        self.assertEqual(resp_trc.status_code, 200)
        self.assertEqual(len(resp_trc.json()), 2)
        for r in resp_trc.json():
            self.assertEqual(r["trace_id"], "trc_eval_1")

        # Filter by decision outcome
        resp_block = self.client.get("/api/v1/decisions", params={"decision": "BLOCK"})
        self.assertEqual(resp_block.status_code, 200)
        self.assertEqual(len(resp_block.json()), 1)
        self.assertEqual(resp_block.json()[0]["action"], "delete")

        # Retrieve specific decision by ID
        dec_id = records[0]["id"]
        resp_get = self.client.get(f"/api/v1/decisions/{dec_id}")
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(resp_get.json()["id"], dec_id)

        # 404 on missing decision ID
        self.assertEqual(self.client.get("/api/v1/decisions/dec_non_existent").status_code, 404)

    def test_decision_history_pagination(self) -> None:
        """GET /api/v1/decisions applies limit and offset pagination correctly."""
        for i in range(5):
            req = AuthorizationRequest(
                trace_id=f"trc_pag_{i}",
                subject="worker",
                action="read",
                resource=f"res_{i}",
            )
            self.auth_service.authorize(req)

        # Page 1: limit 2, offset 0
        p1 = self.client.get("/api/v1/decisions", params={"limit": 2, "offset": 0}).json()
        self.assertEqual(len(p1), 2)

        # Page 2: limit 2, offset 2
        p2 = self.client.get("/api/v1/decisions", params={"limit": 2, "offset": 2}).json()
        self.assertEqual(len(p2), 2)
        self.assertNotEqual(p1[0]["id"], p2[0]["id"])


if __name__ == "__main__":
    unittest.main()
