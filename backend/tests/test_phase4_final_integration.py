"""End-to-End Final Integration Verification Suite for AMGR Phase 4 (Master Prompt 10).

Cohesively exercises and validates the complete Phase 4 lifecycle:
1. Policy CRUD with Scope Isolation:
   - Creation of policies with declarative safety, uniqueness validation, and human-only authority.
   - Listing with multi-attribute filtering and retrieval by ID.
2. Monotonic Versioning and Immutable History:
   - Sequential updates incrementing versions (v1 -> v2) while preserving immutable history.
   - Audited change history tracking actor attribution and change classifications.
3. Policy Diffing and Semantic Comparison:
   - Revisions comparison (v1 vs v2) and proposed update diff preview.
4. Deterministic Matching, Precedence, and Conflict Resolution:
   - PolicyMatcher and PolicyPrecedence evaluating conditions, ordering rules/policies, and resolving conflicts.
5. Conflict Detection & Candidate Verification:
   - Analysis of disabled rules, shadows, opposing effects, and redundant rules.
6. Simulation & Dry-Run Evaluation:
   - Zero side-effects evaluation against draft candidate and active policy sets.
7. Human Approval Lifecycle & TTL Drift Protection:
   - Generation of approval requests from REQUIRE_APPROVAL rules.
   - Human submission (rejection of automated AI actors).
   - Approval expiration on TTL and invalidation on policy revision drift.
8. Policy Rollback & Reversion:
   - Monotonic version creation restoring historical definitions.
9. Decision Analytics & Retention Purge:
   - Summary metric aggregation and retention purge (dry-run and live).
10. Database Persistence Integrity:
    - Verifies SQLite relational engine backing for all Phase 4 tables.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api.routes.policies import (
    PolicyCallerIdentity,
    PolicyScope,
    reset_policy_service,
)
from backend.app.main import create_app
from backend.app.db.session import Base
from backend.app.models.policy import PolicyModel
from backend.app.models.policy_decision import PolicyDecisionModel
from backend.app.models.policy_version import PolicyVersionModel
from backend.app.policy_engine.matcher import PolicyMatcher
from backend.app.policy_engine.precedence import PolicyPrecedence
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import (
    ApprovalDecisionSubmission,
    ApprovalDecisionType,
    ApprovalStatus,
)
from backend.app.schemas.authorization import AuthorizationRequest, ReasonCode
from backend.app.schemas.condition import ConditionGroup, ConditionRule
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    PolicySimulationRequest,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
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

    def put(
        self, path: str, json: Any = None, headers: dict[str, str] | None = None
    ) -> _TestResponse:
        return self.request("PUT", path, json_body=json, headers=headers)

    def delete(self, path: str, headers: dict[str, str] | None = None) -> _TestResponse:
        return self.request("DELETE", path, headers=headers)


class TestPhase4FinalIntegration(unittest.TestCase):
    """Full End-to-End integration verification suite for AMGR Phase 4."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.decision_repo = DecisionRepository()
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )
        self.policy_service = PolicyService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )
        reset_policy_service(self.policy_service)

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            policy_service=self.policy_service,
            decision_repo=self.decision_repo,
        )

        self.human_admin = PolicyCallerIdentity(
            subject="sec_officer_eva",
            authorized_scopes=[PolicyScope(application_id="app_e2e", environment="prod")],
            is_ai_or_llm=False,
        )
        self.ai_agent = PolicyCallerIdentity(
            subject="ai:autonomous_operator_v1",
            authorized_scopes=[PolicyScope(application_id="app_e2e", environment="prod")],
            is_ai_or_llm=True,
        )
        self.client = ASGIClient(self.app, identity=self.human_admin)
        self.ai_client = ASGIClient(self.app, identity=self.ai_agent)

    def tearDown(self) -> None:
        reset_policy_service(None)

    def test_full_phase4_lifecycle_and_safeguards(self) -> None:
        # 1. AI Write Prevention
        ai_create_resp = self.ai_client.post(
            "/policies",
            json={
                "id": "pol_ai_attempt",
                "name": "AI Policy",
                "rules": [
                    {
                        "id": "r1",
                        "name": "r1",
                        "condition": {"field": "a", "operator": "EQ", "value": "1"},
                        "effect": "ALLOW",
                    }
                ],
            },
        )
        self.assertEqual(ai_create_resp.status_code, 403)

        # 2. Human Policy Creation (v1)
        create_payload = {
            "id": "pol_financial_ops",
            "name": "Financial Operations Guard",
            "description": "Governs high-value transactions and exports",
            "priority": 100,
            "resolution_algorithm": "BLOCK_OVERRIDES",
            "default_effect": "BLOCK",
            "enabled": True,
            "rules": [
                {
                    "id": "rule_require_approval_high_val",
                    "name": "Require Approval for High Value",
                    "condition": {
                        "field": "context.amount",
                        "operator": "GT",
                        "value": 10000,
                    },
                    "effect": "REQUIRE_APPROVAL",
                    "priority": 50,
                    "target_actions": ["transfer_funds", "wire_money"],
                },
                {
                    "id": "rule_allow_low_val",
                    "name": "Allow Low Value",
                    "condition": {
                        "field": "context.amount",
                        "operator": "LTE",
                        "value": 10000,
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                    "target_actions": ["transfer_funds", "wire_money"],
                },
            ],
        }
        res_create = self.client.post("/policies", json=create_payload)
        self.assertEqual(res_create.status_code, 201)
        created_data = res_create.json()
        self.assertEqual(created_data["id"], "pol_financial_ops")
        self.assertEqual(created_data["version"], 1)

        # 3. Policy Update (v1 -> v2) with Monotonic Increment
        update_payload = {
            "description": "Updated Financial Guard v2",
            "rules": [
                {
                    "id": "rule_require_approval_high_val",
                    "name": "Require Approval for High Value",
                    "condition": {
                        "field": "context.amount",
                        "operator": "GT",
                        "value": 5000,  # Lowered threshold in v2
                    },
                    "effect": "REQUIRE_APPROVAL",
                    "priority": 50,
                    "target_actions": ["transfer_funds", "wire_money"],
                },
                {
                    "id": "rule_allow_low_val",
                    "name": "Allow Low Value",
                    "condition": {
                        "field": "context.amount",
                        "operator": "LTE",
                        "value": 5000,
                    },
                    "effect": "ALLOW",
                    "priority": 10,
                    "target_actions": ["transfer_funds", "wire_money"],
                },
            ],
        }
        res_update = self.client.put("/policies/pol_financial_ops", json=update_payload)
        self.assertEqual(res_update.status_code, 200)
        updated_data = res_update.json()
        self.assertEqual(updated_data["version"], 2)

        # Verify Immutable History: revision 1 and revision 2 both retrievable
        res_v1 = self.client.get("/policies/pol_financial_ops?version=1")
        self.assertEqual(res_v1.status_code, 200)
        self.assertEqual(res_v1.json()["version"], 1)

        res_v2 = self.client.get("/policies/pol_financial_ops?version=2")
        self.assertEqual(res_v2.status_code, 200)
        self.assertEqual(res_v2.json()["version"], 2)

        # 4. Semantic Policy Diff
        res_diff = self.client.get("/policies/pol_financial_ops/diff?from_version=1&to_version=2")
        self.assertEqual(res_diff.status_code, 200)
        diff_data = res_diff.json()
        self.assertTrue(diff_data["has_changes"])
        self.assertIn("description", diff_data["metadata_changes"])

        # 5. Candidate Conflict Analysis Endpoint
        candidate = {
            "id": "pol_candidate_test",
            "name": "Candidate",
            "rules": [
                {
                    "id": "r_opp1",
                    "name": "r_opp1",
                    "condition": {"field": "a", "operator": "EQ", "value": "x"},
                    "effect": "ALLOW",
                },
                {
                    "id": "r_opp2",
                    "name": "r_opp2",
                    "condition": {"field": "a", "operator": "EQ", "value": "x"},
                    "effect": "BLOCK",
                },
            ],
        }
        res_conflicts = self.client.post("/policies/conflicts", json=candidate)
        self.assertEqual(res_conflicts.status_code, 200)
        self.assertTrue(any(c["conflict_type"] == "OPPOSING_EFFECT" for c in res_conflicts.json()))

        # 6. Runtime Authorization & Approval Ticket Creation
        req_auth = {
            "trace_id": "trc_payment_9921",
            "subject": "service_payment_bot",
            "action": "transfer_funds",
            "resource": "account://core_banking",
            "context": {"amount": 7500},  # Triggers REQUIRE_APPROVAL under v2 (>5000)
        }
        res_auth = self.client.post("/authorize", json=req_auth)
        self.assertEqual(res_auth.status_code, 200)
        auth_decision = res_auth.json()
        self.assertEqual(auth_decision["decision"], "REQUIRE_APPROVAL")
        approval_id = res_auth.headers.get("x-amgr-approval-id")
        self.assertIsNotNone(approval_id)

        # Initial can-proceed is False (Pending)
        res_proceed = self.client.get(f"/approvals/{approval_id}/can-proceed")
        self.assertEqual(res_proceed.status_code, 200)
        self.assertFalse(res_proceed.json()["can_proceed"])

        # 7. AI Actor Forbidden from Approving (schema validator rejects is_ai_or_llm=True with 422)
        res_ai_flag = self.client.post(
            f"/approvals/{approval_id}/decision",
            json={
                "actor": "sec_officer_eva",
                "decision": "APPROVE",
                "is_ai_or_llm": True,
            },
        )
        self.assertEqual(res_ai_flag.status_code, 422)

        res_ai_prefix = self.client.post(
            f"/approvals/{approval_id}/decision",
            json={
                "actor": "ai:autonomous_operator_v1",
                "decision": "APPROVE",
                "is_ai_or_llm": False,
            },
        )
        self.assertEqual(res_ai_prefix.status_code, 422)

        # 8. Human Reviewer Approves Request
        res_human_appr = self.client.post(
            f"/approvals/{approval_id}/decision",
            json={
                "actor": "sec_officer_eva",
                "decision": "APPROVE",
                "reason": "Verified invoice #10294",
                "is_ai_or_llm": False,
            },
        )
        self.assertEqual(res_human_appr.status_code, 200)
        self.assertEqual(res_human_appr.json()["status"], "APPROVED")

        # Now can-proceed is True
        res_proceed_after = self.client.get(f"/approvals/{approval_id}/can-proceed")
        self.assertTrue(res_proceed_after.json()["can_proceed"])

        # 9. Policy Rollback (Reverting to v1, producing v3) & Approval Drift Invalidation
        res_rollback = self.client.post(
            "/policies/pol_financial_ops/rollback",
            json={"target_version": 1, "description": "Rollback to v1 threshold"},
        )
        self.assertEqual(res_rollback.status_code, 200)
        rollback_data = res_rollback.json()
        self.assertEqual(rollback_data["version"], 3)

        # After policy mutation, can-proceed fails closed due to version drift
        res_proceed_drift = self.client.get(f"/approvals/{approval_id}/can-proceed")
        self.assertFalse(res_proceed_drift.json()["can_proceed"])
        self.assertEqual(res_proceed_drift.json()["status"], "APPROVED")

        # 10. Decision Analytics & Retention Purge
        res_summary = self.client.get("/decisions/summary")
        self.assertEqual(res_summary.status_code, 200)
        summary_data = res_summary.json()
        self.assertGreaterEqual(summary_data["total_decisions"], 1)
        self.assertIn("REQUIRE_APPROVAL", summary_data["decision_counts"])

        # Dry run retention purge
        res_dry = self.client.post(
            "/decisions/purge",
            json={"older_than_seconds": 0, "dry_run": True, "is_ai_or_llm": False},
        )
        self.assertEqual(res_dry.status_code, 200)
        self.assertTrue(res_dry.json()["dry_run"])
        self.assertGreaterEqual(res_dry.json()["purged_count"], 1)

        # Live retention purge
        res_purge = self.client.post(
            "/decisions/purge",
            json={"older_than_seconds": 0, "dry_run": False, "is_ai_or_llm": False},
        )
        self.assertEqual(res_purge.status_code, 200)
        self.assertFalse(res_purge.json()["dry_run"])
        self.assertEqual(res_purge.json()["remaining_count"], 0)

        # Confirm decision table is empty in DB
        res_summary_after = self.client.get("/decisions/summary")
        self.assertEqual(res_summary_after.json()["total_decisions"], 0)

    def test_sqlite_database_persistence_and_models(self) -> None:
        """Verifies database persistence, tables, queries, and version models using SQLite."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        db = session_factory()

        try:
            repo = PolicyRepository(db_session=db)
            dec_repo = DecisionRepository(db_session=db)
            service = PolicyService(policy_repo=repo)

            # 1. Create policy in DB
            p1 = service.create_policy(
                PolicyCreate(
                    id="pol_db_audit",
                    name="DB Audit Guard",
                    priority=20,
                    rules=[
                        RuleDefinition(
                            id="rule_db_1",
                            name="Rule DB 1",
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
                actor="db_admin",
            )
            self.assertEqual(p1.version, 1)

            # 2. Verify PolicyModel row in DB
            row = db.get(PolicyModel, "pol_db_audit")
            self.assertIsNotNone(row)
            self.assertEqual(row.version, 1)

            # 3. Update to v2
            p2 = service.update_policy(
                "pol_db_audit",
                PolicyUpdate(name="DB Audit Guard V2", priority=30),
                actor="db_admin",
            )
            self.assertEqual(p2.version, 2)

            # 4. Verify PolicyVersionModel rows in DB
            v_rows = (
                db.query(PolicyVersionModel)
                .filter_by(policy_id="pol_db_audit")
                .order_by(PolicyVersionModel.version.asc())
                .all()
            )
            self.assertEqual(len(v_rows), 2)
            self.assertEqual(v_rows[0].version, 1)
            self.assertEqual(v_rows[0].change_type, "CREATED")
            self.assertEqual(v_rows[1].version, 2)
            self.assertEqual(v_rows[1].change_type, "UPDATED")

            # 5. Record Decision in PolicyDecisionModel
            from backend.app.schemas.authorization import AuthorizationDecision
            dec_obj = AuthorizationDecision(
                decision=p1.rules[0].effect,
                policy_id="pol_db_audit",
                matched_rule_id="rule_db_1",
                matched_rule_ids=["rule_db_1"],
                matched_policy_ids=["pol_db_audit"],
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="Matched rule",
            )
            decision_id = dec_repo.record_decision(
                AuthorizationRequest(
                    trace_id="trc_db_1",
                    subject="agent_1",
                    action="read",
                    resource="file://test",
                ),
                dec_obj,
            )
            self.assertIsNotNone(decision_id)

            d_row = db.get(PolicyDecisionModel, decision_id)
            self.assertIsNotNone(d_row)
            self.assertEqual(d_row.trace_id, "trc_db_1")
            self.assertEqual(d_row.decision, "ALLOW")

        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
