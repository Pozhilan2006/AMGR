"""Focused API integration tests for Member 3 SDK-compatible enforcement endpoint.

Verifies:
- Registration of POST /api/v1/enforcement/authorize exactly once.
- Deterministic request translation via compatibility adapter.
- Outcome mapping to SDK literal contracts ('allow', 'deny', 'review').
- Approval header injection (X-AMGR-Approval-ID) on 'review' decisions.
- Safe fail-closed error handling without leaking internals or synthesizing ALLOW.
- Rejection of synthetic UUID generation for string-slug policy IDs (policy-ID blocker check).
- Coexistence with and preservation of the canonical POST /api/v1/authorize endpoint.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from backend.app.main import create_app
from backend.app.policy_engine.evaluator import evaluate_policies
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.adapter import (
    PolicyIdCompatibilityError,
    SDKAuthorizationDecision,
    SDKAuthorizationRequest,
    SDKComponentRef,
    convert_backend_decision_to_sdk,
    convert_sdk_request_to_backend,
    map_policy_effect_to_sdk_outcome,
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


class TestEnforcementApiIntegration(unittest.TestCase):
    """Integration test suite for POST /api/v1/enforcement/authorize."""

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

        # Standard UUID policy identifiers (starting with a-f to satisfy backend regex)
        self.allow_policy_id = "a0000000-0000-0000-0000-000000000001"
        self.deny_policy_id = "b0000000-0000-0000-0000-000000000002"
        self.review_policy_id = "c0000000-0000-0000-0000-000000000003"

    def test_endpoint_registered_exactly_once(self) -> None:
        """Endpoint /api/v1/enforcement/authorize is registered in OpenAPI and router exactly once."""
        paths = self.app.openapi().get("paths", {})
        self.assertIn(
            "/api/v1/enforcement/authorize",
            paths,
            "Expected /api/v1/enforcement/authorize to be registered in app OpenAPI schema",
        )
        self.assertIn("post", paths["/api/v1/enforcement/authorize"])

        matching_router_routes = [
            route
            for route in self.app.routes
            if hasattr(route, "path")
            and route.path == "/enforcement/authorize"
            and "POST" in getattr(route, "methods", set())
        ]
        # In authorization router directly
        from backend.app.api.authorization import router as auth_router

        matching_in_router = [
            r
            for r in auth_router.routes
            if hasattr(r, "path")
            and r.path == "/enforcement/authorize"
            and "POST" in getattr(r, "methods", set())
        ]
        self.assertEqual(
            len(matching_in_router),
            1,
            f"Expected exactly 1 route on authorization router, found {len(matching_in_router)}",
        )

    def test_valid_sdk_request_evaluates_to_allow_with_uuid_policy(self) -> None:
        """SDK request evaluating to ALLOW returns outcome='allow' and faithful UUID policy_ids."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id=self.allow_policy_id,
                name="Allow document reading",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_allow_read",
                        name="Allow read operation",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="read_document",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        req_id = str(uuid4())
        flow_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "subject": {"component_id": "agent_alpha", "component_type": "agent"},
            "action": "read_document",
            "resource": {"component_id": "doc_vault", "component_type": "database"},
            "data_labels": ["internal"],
            "metadata": {"dept": "engineering"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["outcome"], "allow")
        self.assertEqual(data["request_id"], req_id)
        self.assertIn("decision_id", data)
        self.assertEqual(data["policy_ids"], [self.allow_policy_id])
        self.assertIsInstance(data["reason"], str)

    def test_valid_sdk_request_evaluates_to_deny_with_uuid_policy(self) -> None:
        """SDK request evaluating to BLOCK returns outcome='deny' and faithful UUID policy_ids."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id=self.deny_policy_id,
                name="Block terminal execution",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_block_term",
                        name="Block bash command",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="run_bash",
                        ),
                        effect=PolicyEffect.BLOCK,
                    )
                ],
            )
        )

        req_id = str(uuid4())
        flow_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "subject": {"component_id": "agent_untrusted", "component_type": "agent"},
            "action": "run_bash",
            "resource": {"component_id": "shell", "component_type": "tool"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["outcome"], "deny")
        self.assertEqual(data["request_id"], req_id)
        self.assertEqual(data["policy_ids"], [self.deny_policy_id])

    def test_valid_sdk_request_evaluates_to_review_with_approval_header(self) -> None:
        """SDK request evaluating to REQUIRE_APPROVAL returns outcome='review' and X-AMGR-Approval-ID."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id=self.review_policy_id,
                name="Review wire transfer",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_review_wire",
                        name="Review wire action",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="wire_funds",
                        ),
                        effect=PolicyEffect.REQUIRE_APPROVAL,
                    )
                ],
            )
        )

        req_id = str(uuid4())
        flow_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "subject": {"component_id": "agent_finance", "component_type": "agent"},
            "action": "wire_funds",
            "resource": {"component_id": "bank_api", "component_type": "api"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["outcome"], "review")
        self.assertEqual(data["request_id"], req_id)
        self.assertEqual(data["policy_ids"], [self.review_policy_id])

        # Approval header check
        approval_header = resp.headers.get("x-amgr-approval-id") or resp.headers.get("X-AMGR-Approval-ID")
        self.assertIsNotNone(approval_header)
        self.assertTrue(approval_header.startswith("appr_"))

        # Verify ApprovalRequest in service
        approval_req = self.approval_service.get_approval_request(approval_header)
        self.assertIsNotNone(approval_req)
        self.assertEqual(approval_req.policy_id, self.review_policy_id)
        self.assertEqual(approval_req.id, approval_header)

    def test_default_fallback_maps_cleanly_with_empty_policy_ids(self) -> None:
        """When no policies match, fallback decision maps cleanly to SDK contract with policy_ids=[]."""
        req_id = str(uuid4())
        flow_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "subject": {"component_id": "agent_solo", "component_type": "agent"},
            "action": "unknown_action",
            "resource": {"component_id": "misc", "component_type": "tool"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["outcome"], "deny")  # Default effect in empty repo is BLOCK
        self.assertEqual(data["policy_ids"], [])
        self.assertEqual(data["request_id"], req_id)

    def test_invalid_request_returns_422(self) -> None:
        """Missing required fields or extra forbidden fields return structured 422."""
        # Missing 'subject'
        resp_missing = self.client.post(
            "/api/v1/enforcement/authorize",
            json_data={
                "flow_id": str(uuid4()),
                "action": "read",
                "resource": {"component_id": "res", "component_type": "tool"},
            },
        )
        self.assertEqual(resp_missing.status_code, 422)

        # Extra forbidden field
        resp_extra = self.client.post(
            "/api/v1/enforcement/authorize",
            json_data={
                "flow_id": str(uuid4()),
                "subject": {"component_id": "agent", "component_type": "agent"},
                "action": "read",
                "resource": {"component_id": "res", "component_type": "tool"},
                "unexpected_forbidden_field": True,
            },
        )
        self.assertEqual(resp_extra.status_code, 422)

    def test_unexpected_evaluator_error_returns_500_and_fails_closed(self) -> None:
        """Unexpected internal exceptions return 500 without leaking stack traces or returning ALLOW."""
        mock_auth_service = MagicMock()
        mock_auth_service.authorize.side_effect = RuntimeError("Fatal engine crash")

        app = create_app(auth_service=mock_auth_service)
        client = AsgiTestClient(app)

        payload = {
            "flow_id": str(uuid4()),
            "subject": {"component_id": "agent", "component_type": "agent"},
            "action": "run",
            "resource": {"component_id": "sys", "component_type": "tool"},
        }
        resp = client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["detail"], "Authorization evaluation failed safely.")
        self.assertNotIn("Fatal engine crash", resp.text)
        self.assertNotIn("allow", resp.text.lower())

    def test_persistence_failure_returns_500_and_fails_closed(self) -> None:
        """Persistence failures fail closed with 500 and do not falsely claim success."""
        mock_decision_repo = MagicMock()
        mock_decision_repo.record_decision.side_effect = RuntimeError("Storage write failed")

        auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            decision_repo=mock_decision_repo,
        )
        app = create_app(auth_service=auth_service)
        client = AsgiTestClient(app)

        payload = {
            "flow_id": str(uuid4()),
            "subject": {"component_id": "agent", "component_type": "agent"},
            "action": "execute",
            "resource": {"component_id": "tool_x", "component_type": "tool"},
        }
        resp = client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["detail"], "Authorization evaluation failed safely.")
        self.assertNotIn("allow", resp.text.lower())

    def test_string_slug_policy_id_preserved_faithfully_in_sdk_response(self) -> None:
        """String slug policy IDs (e.g. 'pol_read_allow') are preserved faithfully in SDK response.

        Verifies that string policy identifiers evaluate and serialize without synthetic UUID invention
        or data loss, adhering to the updated SDK contract.
        """
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_read_allow",
                name="Slug policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_slug_allow",
                        name="Allow read",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="read_slug",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        req_id = str(uuid4())
        flow_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "subject": {"component_id": "agent_reader", "component_type": "agent"},
            "action": "read_slug",
            "resource": {"component_id": "res_slug", "component_type": "tool"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["outcome"], "allow")
        self.assertEqual(data["request_id"], req_id)
        self.assertEqual(data["policy_ids"], ["pol_read_allow"])

        # Verify SDK client model parses response payload directly
        try:
            from amgr_sdk.enforcement.client import AuthorizationDecision as SDKClientDecision
        except ModuleNotFoundError:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk"))
            from amgr_sdk.enforcement.client import AuthorizationDecision as SDKClientDecision

        sdk_decision = SDKClientDecision.model_validate(data)
        self.assertEqual(sdk_decision.outcome, "allow")
        self.assertEqual(sdk_decision.policy_ids, ["pol_read_allow"])

    def test_canonical_authorize_endpoint_remains_fully_compatible(self) -> None:
        """Original POST /api/v1/authorize remains completely operational and unaffected."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_canonical_test",
                name="Canonical endpoint policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_canonical",
                        name="Rule canonical",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="ping_canonical",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        canonical_payload = {
            "trace_id": "trc_canonical_01",
            "subject": "service_worker",
            "action": "ping_canonical",
            "resource": "service://health",
            "context": {},
        }
        resp = self.client.post("/api/v1/authorize", json_data=canonical_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["decision"], "ALLOW")
        self.assertEqual(data["policy_id"], "pol_canonical_test")

    def test_sdk_request_correlation_and_audit_traceability_persisted(self) -> None:
        """SDK request correlation fields (request_id, flow_id, correlation_id, data_labels) persist faithfully in decision history."""
        self.policy_repo.create_policy(
            PolicyCreate(
                id=self.allow_policy_id,
                name="Audit trace policy",
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_audit_allow",
                        name="Allow audit",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="audit_query",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        req_id = str(uuid4())
        flow_id = str(uuid4())
        corr_id = str(uuid4())
        payload = {
            "request_id": req_id,
            "flow_id": flow_id,
            "correlation_id": corr_id,
            "subject": {"component_id": "auditor_01", "component_type": "agent", "name": "Auditor"},
            "action": "audit_query",
            "resource": {"component_id": "secure_log", "component_type": "database"},
            "data_labels": ["pci", "sox"],
            "metadata": {"dept": "compliance", "ticket": "SEC-900"},
        }

        resp = self.client.post("/api/v1/enforcement/authorize", json_data=payload)
        self.assertEqual(resp.status_code, 200)

        # Retrieve recorded decision via DecisionRepository
        decisions = self.auth_service.decision_repo.list_decisions(trace_id=flow_id)
        self.assertEqual(len(decisions), 1)
        record = decisions[0]

        # Verify trace linkage
        self.assertEqual(record["trace_id"], flow_id)
        self.assertEqual(record["action"], "audit_query")
        self.assertEqual(record["resource"], "database://secure_log")
        self.assertEqual(record["decision"], "ALLOW")
        self.assertEqual(record["policy_id"], self.allow_policy_id)

        # Verify context audit traceability
        ctx = record["context_data"]
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["flow_id"], flow_id)
        self.assertEqual(ctx["sdk_request_id"], req_id)
        self.assertEqual(ctx["correlation_id"], corr_id)
        self.assertEqual(ctx["data_labels"], ["pci", "sox"])
        self.assertEqual(ctx["dept"], "compliance")
        self.assertEqual(ctx["ticket"], "SEC-900")
        self.assertEqual(ctx["subject_details"]["component_id"], "auditor_01")
        self.assertEqual(ctx["resource_details"]["component_id"], "secure_log")


if __name__ == "__main__":
    unittest.main()

