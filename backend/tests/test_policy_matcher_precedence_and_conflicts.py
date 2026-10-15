"""Tests for AMGR Phase 4 Master Prompt 9: Policy Matcher, Precedence Resolution, and Candidate Conflicts API.

Verifies:
1. PolicyMatcher condition matching, rule matching, and attribute resolution.
2. PolicyPrecedence rule ordering, policy tie-breaking, priority tier grouping, and rule/tier conflict resolution.
3. Multi-policy active policy dry-run simulation via POST /policies/simulate (evaluating active policies without side effects).
4. Candidate policy conflict analysis endpoint (POST /policies/conflicts) detecting disabled, shadowed, and opposing rules.
5. Scope isolation and security validation on candidate conflict analysis.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from backend.app.api.routes.policies import (
    PolicyCallerIdentity,
    PolicyScope,
    reset_policy_service,
)
from backend.app.main import create_app
from backend.app.policy_engine.matcher import PolicyMatcher
from backend.app.policy_engine.precedence import PolicyPrecedence
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import AuthorizationRequest, ReasonCode
from backend.app.schemas.condition import ConditionGroup, ConditionRule
from backend.app.schemas.policy import (
    ConflictDetectionResult,
    PolicyCreate,
    PolicyRead,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.policy_service import PolicyService


class _TestResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = body

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class ASGIClient:
    def __init__(self, app: Any, identity: PolicyCallerIdentity | None = None) -> None:
        self.app = app
        self.identity = identity

    def request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> _TestResponse:
        req_headers = [(b"host", b"testserver")]
        if headers:
            for k, v in headers.items():
                req_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

        body_bytes = b""
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            req_headers.append((b"content-type", b"application/json"))

        response_status = 0
        response_headers: dict[str, str] = {}
        response_body = bytearray()

        query_string = b""
        if "?" in path:
            path, qs = path.split("?", 1)
            query_string = qs.encode("latin-1")

        async def run() -> None:
            nonlocal response_status
            scope = {
                "type": "http",
                "method": method,
                "path": path,
                "raw_path": path.encode("latin-1"),
                "query_string": query_string,
                "headers": req_headers,
                "state": {},
            }
            if self.identity is not None:
                scope["state"]["identity"] = self.identity

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = message["status"]
                    for k, v in message.get("headers", []):
                        response_headers[k.decode("latin-1")] = v.decode("latin-1")
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body", b""))

            await self.app(scope, receive, send)

        asyncio.run(run())
        return _TestResponse(response_status, response_headers, bytes(response_body))

    def get(self, path: str, headers: dict[str, str] | None = None) -> _TestResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self, path: str, json: Any = None, headers: dict[str, str] | None = None
    ) -> _TestResponse:
        return self.request("POST", path, json_body=json, headers=headers)


class TestPolicyMatcherPrecedenceAndConflicts(unittest.TestCase):
    """Test suite for PolicyMatcher, PolicyPrecedence, and candidate conflict detection."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.decision_repo = DecisionRepository()
        self.policy_service = PolicyService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        reset_policy_service(self.policy_service)
        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            policy_service=self.policy_service,
            decision_repo=self.decision_repo,
        )

        self.sec_admin = PolicyCallerIdentity(
            subject="security_admin_carl",
            authorized_scopes=[PolicyScope(application_id="app_sec", environment="prod")],
            is_ai_or_llm=False,
        )
        self.client = ASGIClient(self.app, identity=self.sec_admin)

    def tearDown(self) -> None:
        reset_policy_service(None)

    def test_policy_matcher_condition_and_rule_evaluation(self) -> None:
        matcher = PolicyMatcher()
        self.assertTrue(matcher._implemented)

        cond = ConditionRule(field="context.score", operator=ConditionOperator.GTE, value=80)
        matched, missing = matcher.match_condition(cond, {"context": {"score": 85}})
        self.assertTrue(matched)
        self.assertEqual(missing, [])

        matched, missing = matcher.match_condition(cond, {"context": {"score": 70}})
        self.assertFalse(matched)
        self.assertEqual(missing, [])

        matched, missing = matcher.match_condition(cond, {})
        self.assertFalse(matched)
        self.assertIn("context.score", missing)

        rule = RuleDefinition(
            id="rule_block_prod_sql",
            name="Block Prod SQL",
            condition=ConditionRule(field="environment", operator=ConditionOperator.EQ, value="prod"),
            effect=PolicyEffect.BLOCK,
            priority=100,
            target_actions=["sql_query"],
        )
        req_match = AuthorizationRequest(
            trace_id="trc_1",
            subject="analyst_bob",
            action="sql_query",
            resource="db://customer",
            context={"environment": "prod"},
        )
        req_diff_action = AuthorizationRequest(
            trace_id="trc_2",
            subject="analyst_bob",
            action="read_file",
            resource="db://customer",
            context={"environment": "prod"},
        )

        matched, scope_matched, missing = matcher.match_rule(rule, req_match)
        self.assertTrue(matched)
        self.assertTrue(scope_matched)

        matched, scope_matched, missing = matcher.match_rule(rule, req_diff_action)
        self.assertFalse(matched)
        self.assertFalse(scope_matched)

        # General match interface
        self.assertTrue(matcher.match(rule, req_match))
        self.assertFalse(matcher.match(rule, req_diff_action))
        self.assertTrue(matcher.match(cond, {"context": {"score": 90}}))

    def test_policy_precedence_ordering_and_tier_conflict_resolution(self) -> None:
        precedence = PolicyPrecedence()
        self.assertTrue(precedence._implemented)

        r1 = RuleDefinition(
            id="r1",
            name="Rule 1",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value="1"),
            effect=PolicyEffect.ALLOW,
            priority=10,
        )
        r2 = RuleDefinition(
            id="r2",
            name="Rule 2",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value="2"),
            effect=PolicyEffect.BLOCK,
            priority=50,
        )
        ordered_rules = precedence.order_rules([r1, r2])
        self.assertEqual([r.id for r in ordered_rules], ["r2", "r1"])

        p_low = PolicyCreate(
            id="pol_b",
            name="B",
            priority=10,
            rules=[r1],
        )
        p_high_a = PolicyCreate(
            id="pol_a",
            name="A",
            priority=50,
            rules=[r2],
        )
        p_high_z = PolicyCreate(
            id="pol_z",
            name="Z",
            priority=50,
            rules=[r2],
        )
        ordered_policies = precedence.order_policies([p_low, p_high_z, p_high_a])
        # Priority descending, ID ascending for ties
        self.assertEqual([p.id for p in ordered_policies], ["pol_a", "pol_z", "pol_b"])

        tiers = precedence.group_by_priority_tier([p_low, p_high_z, p_high_a])
        self.assertEqual(len(tiers), 2)
        self.assertEqual(tiers[0][0], 50)
        self.assertEqual([p.id for p in tiers[0][1]], ["pol_a", "pol_z"])
        self.assertEqual(tiers[1][0], 10)
        self.assertEqual([p.id for p in tiers[1][1]], ["pol_b"])

        # Tier conflict resolution
        res_block = precedence.resolve_tier_conflict(
            [PolicyEffect.ALLOW, PolicyEffect.BLOCK],
            RuleResolutionAlgorithm.BLOCK_OVERRIDES,
        )
        self.assertEqual(res_block, PolicyEffect.BLOCK)

        res_allow = precedence.resolve_tier_conflict(
            [PolicyEffect.ALLOW, PolicyEffect.BLOCK],
            RuleResolutionAlgorithm.ALLOW_OVERRIDES,
        )
        self.assertEqual(res_allow, PolicyEffect.ALLOW)

        # Dispatch resolve
        resolved_rule = precedence.resolve([r1, r2], algorithm=RuleResolutionAlgorithm.FIRST_APPLICABLE)
        self.assertEqual(resolved_rule.id, "r2")

    def test_multi_policy_active_simulation_without_side_effects(self) -> None:
        # Create an active policy in repo
        pol = PolicyCreate(
            id="pol_active_guard",
            name="Active Security Guard",
            priority=100,
            rules=[
                RuleDefinition(
                    id="rule_block_unauthorized_env",
                    name="Block non-prod in prod scope",
                    condition=ConditionRule(
                        field="context.action_tag",
                        operator=ConditionOperator.EQ,
                        value="dangerous",
                    ),
                    effect=PolicyEffect.BLOCK,
                    priority=10,
                )
            ],
        )
        self.policy_repo.create_policy(pol, actor="security_admin_carl")

        # Simulate without candidate_policy or policy_id: simulates active policy set
        req = {
            "request": {
                "trace_id": "trc_sim_multi_1",
                "subject": "service_reporter",
                "action": "export",
                "resource": "s3://vault",
                "context": {"action_tag": "dangerous"},
            }
        }
        resp = self.client.post("/policies/simulate", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["matched_rule_id"], "rule_block_unauthorized_env")
        self.assertEqual(data["policy_id"], "pol_active_guard")

        # Verify zero side effects: no decision records and no approvals
        self.assertEqual(len(self.decision_repo.list_decisions()), 0)
        self.assertEqual(len(self.approval_service.list_all_requests()), 0)

    def test_candidate_policy_conflict_detection_endpoint(self) -> None:
        candidate = {
            "id": "pol_candidate_with_conflicts",
            "name": "Candidate Policy",
            "priority": 10,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_allow_all",
                    "name": "Allow all",
                    "condition": {
                        "field": "context.scope",
                        "operator": "EQ",
                        "value": "internal",
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                },
                {
                    "id": "rule_block_all",
                    "name": "Block all opposing",
                    "condition": {
                        "field": "context.scope",
                        "operator": "EQ",
                        "value": "internal",
                    },
                    "effect": "BLOCK",
                    "priority": 10,
                },
                {
                    "id": "rule_disabled_item",
                    "name": "Disabled rule",
                    "condition": {
                        "field": "context.tag",
                        "operator": "EQ",
                        "value": "test",
                    },
                    "effect": "ALLOW",
                    "priority": 5,
                    "enabled": False,
                },
            ],
        }

        resp = self.client.post("/policies/conflicts", json=candidate)
        self.assertEqual(resp.status_code, 200)
        conflicts = resp.json()
        self.assertGreaterEqual(len(conflicts), 2)
        conflict_types = [c["conflict_type"] for c in conflicts]
        self.assertIn("DISABLED_RULE", conflict_types)
        self.assertIn("OPPOSING_EFFECT", conflict_types)

    def test_candidate_policy_conflict_detection_rejects_unsafe_code(self) -> None:
        unsafe_candidate = {
            "id": "pol_unsafe_injection",
            "name": "Unsafe Policy",
            "priority": 10,
            "rules": [
                {
                    "id": "rule_eval",
                    "name": "Eval rule",
                    "condition": {
                        "field": "action",
                        "operator": "EQ",
                        "value": "eval('__import__(\"os\").system(\"calc\")')",
                    },
                    "effect": "BLOCK",
                    "priority": 1,
                }
            ],
        }
        resp = self.client.post("/policies/conflicts", json=unsafe_candidate)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("executable expression", resp.json()["detail"].lower())

    def test_candidate_policy_conflict_detection_unauthorized_scope(self) -> None:
        # Caller only has scope (app_sec, prod). Rule requiring (app_other, staging) should fail scope
        out_of_scope_candidate = {
            "id": "pol_out_of_scope",
            "name": "Out of scope policy",
            "priority": 10,
            "rules": [
                {
                    "id": "rule_scope_restricted",
                    "name": "Restricted scope",
                    "condition": {
                        "field": "context.environment",
                        "operator": "EQ",
                        "value": "staging",
                    },
                    "effect": "BLOCK",
                    "priority": 1,
                }
            ],
        }
        resp = self.client.post("/policies/conflicts", json=out_of_scope_candidate)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("outside caller's authorized scope", resp.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
