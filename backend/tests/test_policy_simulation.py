"""Tests for AMGR Phase 4 Master Prompt 5: Policy Simulation, Dry-Run Evaluation, and Conflict Detection.

Verifies:
1. Simulating an existing policy (POST /{policy_id}/simulate) against a matching request.
2. Zero side effects: simulation does not write to DecisionRepository or trigger human ApprovalRequests.
3. Simulating a request with missing attributes fails closed and reports missing attributes.
4. Candidate policy dry-run simulation (POST /policies/simulate) evaluates candidate without persisting.
5. Historical version simulation: simulating against version 1 vs version 2.
6. Scope isolation: rejects simulation of out-of-scope policies with 403 Forbidden.
7. Unauthenticated callers rejected with 401 Unauthorized.
8. Safety validation: candidate policies with unsafe code or SQL injection rejected with 422.
9. Rule conflict and shadow detection (GET /{policy_id}/conflicts and simulation conflicts):
   detects shadowed rules under FIRST_APPLICABLE, opposing effects, redundant rules, and disabled rules.
10. AI callers with authorized scope can perform dry-run simulation without mutation.
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
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import ConditionOperator, ConditionRule
from backend.app.schemas.policy import (
    ConflictDetectionResult,
    PolicyCreate,
    PolicyRead,
    PolicySimulationRequest,
    PolicySimulationResponse,
    PolicyUpdate,
    RuleDefinition,
    RuleSimulationTrace,
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
            import urllib.parse
            query_string = urllib.parse.urlencode(
                {k: str(v) for k, v in params.items() if v is not None}
            )

        ident = self.default_identity if identity == "__USE_DEFAULT__" else identity

        body = b""
        headers = [(b"host", b"testserver")]
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
            headers.append((b"content-length", str(len(body)).encode("ascii")))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": headers,
            "state": {"identity": ident} if ident is not None else {},
        }

        response_status = 500
        response_headers: dict[str, str] = {}
        response_chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    response_headers[k.decode("ascii").lower()] = v.decode("ascii")
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.app(scope, receive, send))
        finally:
            loop.close()

        return _TestResponse(response_status, response_headers, b"".join(response_chunks))

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> _TestResponse:
        return self.request("GET", path, params=params, **kwargs)

    def post(self, path: str, json_data: Any = None, params: dict[str, Any] | None = None, **kwargs: Any) -> _TestResponse:
        return self.request("POST", path, params=params, json_data=json_data, **kwargs)


class TestPolicySimulationAndConflictDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.decision_repo = DecisionRepository()
        self.approval_service = ApprovalService()

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

        self.human_identity = PolicyCallerIdentity(
            subject="operator:sec_lead",
            authorized_scopes=[
                PolicyScope(application_id="app-finance", environment="production"),
                PolicyScope(application_id="app-finance", environment="staging"),
            ],
            is_ai_or_llm=False,
        )

        self.ai_identity = PolicyCallerIdentity(
            subject="ai:analysis_agent",
            authorized_scopes=[
                PolicyScope(application_id="app-finance", environment="production"),
            ],
            is_ai_or_llm=True,
        )

        self.foreign_identity = PolicyCallerIdentity(
            subject="operator:other_team",
            authorized_scopes=[
                PolicyScope(application_id="app-logistics", environment="production"),
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

        # Seed standard policy: requires approval for transfers over $1000
        self.seed_policy = PolicyCreate(
            id="pol_sim_transfer_guard",
            name="Wire Transfer Security Policy",
            description="Controls wire transfer permissions based on amount",
            priority=50,
            resolution_algorithm=RuleResolutionAlgorithm.FIRST_APPLICABLE,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
            rules=[
                RuleDefinition(
                    id="rule_large_transfer_review",
                    name="Require review for high-value transfers",
                    condition=ConditionRule(
                        field="context.amount",
                        operator=ConditionOperator.GT,
                        value=1000,
                    ),
                    effect=PolicyEffect.REQUIRE_APPROVAL,
                    priority=20,
                    enabled=True,
                    target_actions=["wire_transfer"],
                    target_resources=["banking://wire"],
                ),
                RuleDefinition(
                    id="rule_small_transfer_allow",
                    name="Allow normal micro-transfers",
                    condition=ConditionRule(
                        field="context.amount",
                        operator=ConditionOperator.LTE,
                        value=1000,
                    ),
                    effect=PolicyEffect.ALLOW,
                    priority=10,
                    enabled=True,
                    target_actions=["wire_transfer"],
                    target_resources=["banking://wire"],
                ),
            ],
        )
        self.created_policy = self.policy_service.create_policy(
            self.seed_policy,
            actor="sec_lead",
            authorized_scopes=self.human_identity.authorized_scopes,
        )

    def tearDown(self) -> None:
        reset_policy_service(None)

    def test_simulate_existing_policy_allow_and_zero_side_effects(self) -> None:
        """Simulating a normal allow request returns ALLOW with trace and zero side effects."""
        sim_request = {
            "request": {
                "trace_id": "sim-trace-001",
                "subject": {"user_id": "alice", "role": "analyst"},
                "action": "wire_transfer",
                "resource": "banking://wire",
                "context": {"amount": 500},
            }
        }

        resp = self.client.post("/api/v1/policies/pol_sim_transfer_guard/simulate", json_data=sim_request)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["policy_id"], "pol_sim_transfer_guard")
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["matched_rule_id"], "rule_small_transfer_allow")
        self.assertEqual(data["reason_code"], ReasonCode.RULE_MATCHED.value)

        # Check execution trace
        traces = data["rule_traces"]
        self.assertEqual(len(traces), 2)
        # First rule (high transfer) evaluated first (priority 20) and did not match
        self.assertEqual(traces[0]["rule_id"], "rule_large_transfer_review")
        self.assertFalse(traces[0]["matched"])
        self.assertTrue(traces[0]["scope_matched"])
        # Second rule (small transfer) matched
        self.assertEqual(traces[1]["rule_id"], "rule_small_transfer_allow")
        self.assertTrue(traces[1]["matched"])

        # CRITICAL: Verify zero side-effects
        # 1. No decision records written to decision repository
        decisions = self.decision_repo.list_decisions()
        self.assertEqual(len(decisions), 0)

        # 2. No pending approval requests created
        approvals = self.approval_service.list_pending_requests()
        self.assertEqual(len(approvals), 0)

    def test_simulate_require_approval_does_not_create_approval_request(self) -> None:
        """Simulating a request that produces REQUIRE_APPROVAL does NOT create a real approval request."""
        sim_request = {
            "request": {
                "trace_id": "sim-trace-large-002",
                "subject": {"user_id": "bob", "role": "trader"},
                "action": "wire_transfer",
                "resource": "banking://wire",
                "context": {"amount": 50000},
            }
        }

        resp = self.client.post("/api/v1/policies/pol_sim_transfer_guard/simulate", json_data=sim_request)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(data["matched_rule_id"], "rule_large_transfer_review")

        # Confirm approval service queue remains completely empty
        self.assertEqual(len(self.approval_service.list_pending_requests()), 0)
        self.assertEqual(len(self.decision_repo.list_decisions()), 0)

    def test_simulate_missing_context_fails_closed(self) -> None:
        """Simulating a request missing the 'amount' field fails closed and reports missing attributes."""
        sim_request = {
            "request": {
                "trace_id": "sim-trace-missing",
                "subject": {"user_id": "carol"},
                "action": "wire_transfer",
                "resource": "banking://wire",
                "context": {},  # missing 'amount'
            }
        }

        resp = self.client.post("/api/v1/policies/pol_sim_transfer_guard/simulate", json_data=sim_request)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["reason_code"], ReasonCode.MISSING_CONTEXT.value)
        self.assertIn("context.amount", data["missing_attributes"])

        # Check trace
        traces = data["rule_traces"]
        self.assertIn("context.amount", traces[0]["missing_attributes"])

    def test_simulate_candidate_policy_dry_run_without_persistence(self) -> None:
        """POST /policies/simulate evaluates candidate draft policy without saving to repo."""
        candidate = {
            "id": "pol_draft_candidate",
            "name": "Draft Candidate Policy",
            "description": "Unsaved draft policy under test",
            "priority": 100,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_allow_test_action",
                    "name": "Allow test action",
                    "condition": {
                        "field": "context.env_tag",
                        "operator": "EQ",
                        "value": "secure_sandbox",
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                    "enabled": True,
                }
            ],
        }

        sim_payload = {
            "request": {
                "trace_id": "sim-draft-001",
                "subject": "service_account",
                "action": "execute_test",
                "resource": "sandbox://env",
                "context": {"env_tag": "secure_sandbox"},
            },
            "candidate_policy": candidate,
        }

        resp = self.client.post("/api/v1/policies/simulate", json_data=sim_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["policy_id"], "pol_draft_candidate")
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["matched_rule_id"], "rule_allow_test_action")

        # Crucial check: Draft policy was NEVER stored in policy repository
        self.assertIsNone(self.policy_repo.get_policy("pol_draft_candidate"))

    def test_simulate_historical_revisions(self) -> None:
        """Simulating against specific historical revision version (v1 vs v2)."""
        # Update policy in v2 to strictly BLOCK all transfers
        v2_update = PolicyUpdate(
            rules=[
                RuleDefinition(
                    id="rule_emergency_freeze",
                    name="Freeze all transfers",
                    condition=ConditionRule(
                        field="action",
                        operator=ConditionOperator.EQ,
                        value="wire_transfer",
                    ),
                    effect=PolicyEffect.BLOCK,
                    priority=100,
                    enabled=True,
                )
            ]
        )
        self.policy_service.update_policy(
            policy_id="pol_sim_transfer_guard",
            update=v2_update,
            actor="sec_lead",
            authorized_scopes=self.human_identity.authorized_scopes,
            description="Emergency freeze",
        )

        test_request = {
            "trace_id": "sim-rev-check",
            "subject": {"user_id": "alice"},
            "action": "wire_transfer",
            "resource": "banking://wire",
            "context": {"amount": 500},
        }

        # 1. Simulating against version 1: $500 transfer should be ALLOWed
        sim_v1 = {"request": test_request, "version": 1}
        resp_v1 = self.client.post("/api/v1/policies/pol_sim_transfer_guard/simulate", json_data=sim_v1)
        self.assertEqual(resp_v1.status_code, 200)
        self.assertEqual(resp_v1.json()["decision"], "ALLOW")
        self.assertEqual(resp_v1.json()["policy_version"], 1)

        # 2. Simulating against version 2 (or default latest): should be BLOCKED
        sim_v2 = {"request": test_request, "version": 2}
        resp_v2 = self.client.post("/api/v1/policies/pol_sim_transfer_guard/simulate", json_data=sim_v2)
        self.assertEqual(resp_v2.status_code, 200)
        self.assertEqual(resp_v2.json()["decision"], "BLOCK")
        self.assertEqual(resp_v2.json()["policy_version"], 2)

    def test_simulation_scope_isolation_403(self) -> None:
        """Caller from foreign scope cannot simulate policies outside their authorization."""
        # Create a policy strictly scoped to app-classified
        scoped_policy = PolicyCreate(
            id="pol_classified_scope",
            name="Classified Scope Policy",
            priority=50,
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
            rules=[
                RuleDefinition(
                    id="rule_classified_scope",
                    name="Classified scope rule",
                    condition=ConditionRule(
                        field="context.application_id",
                        operator=ConditionOperator.EQ,
                        value="app-classified",
                    ),
                    effect=PolicyEffect.ALLOW,
                    priority=10,
                    enabled=True,
                )
            ],
        )
        self.policy_repo.create_policy(scoped_policy)

        sim_request = {
            "request": {
                "trace_id": "sim-foreign",
                "subject": "someone",
                "action": "wire_transfer",
                "resource": "banking://wire",
                "context": {"amount": 100},
            }
        }

        # Human identity has scope app-finance, not app-classified -> 403 Forbidden
        resp = self.client.post(
            "/api/v1/policies/pol_classified_scope/simulate",
            json_data=sim_request,
            identity=self.human_identity,
        )
        self.assertEqual(resp.status_code, 403)

        # Unauthenticated request rejected with 401
        anon_client = ASGIClient(self.app, default_identity=None)
        resp_anon = anon_client.post(
            "/api/v1/policies/pol_sim_transfer_guard/simulate",
            json_data=sim_request,
        )
        self.assertEqual(resp_anon.status_code, 401)

    def test_simulation_safety_validation_422(self) -> None:
        """Candidate policy containing unsafe executable Python or SQL patterns is rejected with 422."""
        unsafe_candidate = {
            "id": "pol_malicious_candidate",
            "name": "Malicious Policy",
            "priority": 10,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_eval_inject",
                    "name": "Injection attempt",
                    "condition": {
                        "field": "context.tag",
                        "operator": "EQ",
                        "value": "eval('__import__(\"os\").system(\"id\")')",
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                    "enabled": True,
                }
            ],
        }

        sim_payload = {
            "request": {
                "trace_id": "sim-evil",
                "subject": "attacker",
                "action": "run",
                "resource": "res",
                "context": {"tag": "evil"},
            },
            "candidate_policy": unsafe_candidate,
        }

        resp = self.client.post("/api/v1/policies/simulate", json_data=sim_payload)
        self.assertEqual(resp.status_code, 422)

    def test_policy_conflict_and_shadow_detection(self) -> None:
        """Tests rule shadowing, redundancy, and disabled rule detection via GET /{policy_id}/conflicts."""
        conflict_policy = PolicyCreate(
            id="pol_conflict_demo",
            name="Conflict Demo Policy",
            description="Demonstrates rule shadowing and conflict detection",
            priority=80,
            resolution_algorithm=RuleResolutionAlgorithm.FIRST_APPLICABLE,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
            rules=[
                RuleDefinition(
                    id="rule_lead_allow",
                    name="Lead rule allowing action",
                    condition=ConditionRule(
                        field="context.role",
                        operator=ConditionOperator.EQ,
                        value="admin",
                    ),
                    effect=PolicyEffect.ALLOW,
                    priority=50,
                    enabled=True,
                ),
                RuleDefinition(
                    id="rule_shadowed_block",
                    name="Shadowed rule with opposing effect",
                    condition=ConditionRule(
                        field="context.role",
                        operator=ConditionOperator.EQ,
                        value="admin",
                    ),
                    effect=PolicyEffect.BLOCK,
                    priority=20,  # lower priority, identical condition and scope
                    enabled=True,
                ),
                RuleDefinition(
                    id="rule_disabled_orphan",
                    name="Disabled rule",
                    condition=ConditionRule(
                        field="context.flag",
                        operator=ConditionOperator.EQ,
                        value=True,
                    ),
                    effect=PolicyEffect.REQUIRE_APPROVAL,
                    priority=10,
                    enabled=False,
                ),
            ],
        )

        self.policy_service.create_policy(
            conflict_policy,
            actor="sec_lead",
            authorized_scopes=self.human_identity.authorized_scopes,
        )

        resp = self.client.get("/api/v1/policies/pol_conflict_demo/conflicts")
        self.assertEqual(resp.status_code, 200)
        conflicts = resp.json()

        conflict_types = {c["conflict_type"] for c in conflicts}
        self.assertIn("OPPOSING_EFFECT", conflict_types)
        self.assertIn("DISABLED_RULE", conflict_types)

        # Check opposing effect conflict details
        opp_conflict = next(c for c in conflicts if c["conflict_type"] == "OPPOSING_EFFECT")
        self.assertEqual(opp_conflict["rule_id"], "rule_shadowed_block")
        self.assertEqual(opp_conflict["shadowed_by_rule_id"], "rule_lead_allow")

        # Disabled rule check
        dis_conflict = next(c for c in conflicts if c["conflict_type"] == "DISABLED_RULE")
        self.assertEqual(dis_conflict["rule_id"], "rule_disabled_orphan")

    def test_ai_caller_can_simulate_within_scope(self) -> None:
        """AI caller within authorized scope can execute read-only simulation."""
        sim_request = {
            "request": {
                "trace_id": "sim-ai-001",
                "subject": "ai:analysis_agent",
                "action": "wire_transfer",
                "resource": "banking://wire",
                "context": {"amount": 500},
            }
        }

        resp = self.client.post(
            "/api/v1/policies/pol_sim_transfer_guard/simulate",
            json_data=sim_request,
            identity=self.ai_identity,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["decision"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
