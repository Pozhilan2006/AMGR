"""Unit tests for Member 1 SDK request compatibility adapter.

Verifies deterministic translation of SDK-format authorization requests into canonical
backend AuthorizationRequest models, resource URI normalization, input validation,
context preservation, and integration with the Phase 2 deterministic policy engine.
"""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError

from backend.app.policy_engine.evaluator import evaluate_policies
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.adapter import (
    SDKAuthorizationDecision,
    SDKAuthorizationRequest,
    SDKComponentRef,
    convert_backend_decision_to_sdk,
    convert_sdk_request_to_backend,
    map_policy_effect_to_sdk_outcome,
    normalize_resource_ref,
)
from backend.app.schemas.authorization import AuthorizationDecision, ReasonCode
from backend.app.schemas.condition import ConditionRule
from backend.app.schemas.policy import PolicyCreate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


class TestSDKRequestAdapter(unittest.TestCase):
    """Verifies typed conversion of SDK authorization requests to backend contracts."""

    def setUp(self) -> None:
        self.flow_id = uuid4()
        self.correlation_id = uuid4()
        self.request_id = uuid4()
        self.subject = SDKComponentRef(
            component_id="agent_analyst_01",
            component_type="agent",
            name="Analyst Agent",
        )
        self.resource = SDKComponentRef(
            component_id="customer_vault",
            component_type="database",
            name="Customer Vault DB",
        )

    def test_valid_request_conversion(self) -> None:
        """SDK request with all fields converts deterministically to canonical backend schema."""
        sdk_req = SDKAuthorizationRequest(
            request_id=self.request_id,
            flow_id=self.flow_id,
            correlation_id=self.correlation_id,
            subject=self.subject,
            action="query_records",
            resource=self.resource,
            data_labels=["confidential", "pii"],
            metadata={"environment": "production", "table": "users"},
        )

        backend_req = convert_sdk_request_to_backend(sdk_req)

        # Core fields verification
        self.assertEqual(backend_req.trace_id, str(self.flow_id))
        self.assertEqual(backend_req.action, "query_records")
        self.assertEqual(backend_req.resource, "database://customer_vault")

        # Subject verification
        expected_subject = {
            "component_id": "agent_analyst_01",
            "component_type": "agent",
            "name": "Analyst Agent",
        }
        self.assertEqual(backend_req.subject, expected_subject)

        # Context preservation
        self.assertEqual(backend_req.context["environment"], "production")
        self.assertEqual(backend_req.context["table"], "users")
        self.assertEqual(backend_req.context["sdk_request_id"], str(self.request_id))
        self.assertEqual(backend_req.context["flow_id"], str(self.flow_id))
        self.assertEqual(backend_req.context["correlation_id"], str(self.correlation_id))
        self.assertEqual(backend_req.context["data_labels"], ["confidential", "pii"])
        self.assertEqual(backend_req.context["resource_details"]["component_id"], "customer_vault")

        # Non-mutation guarantee
        self.assertEqual(sdk_req.action, "query_records")
        self.assertEqual(sdk_req.metadata["environment"], "production")

    def test_missing_correlation_id(self) -> None:
        """SDK request with correlation_id=None converts cleanly without failure."""
        sdk_req = SDKAuthorizationRequest(
            request_id=self.request_id,
            flow_id=self.flow_id,
            correlation_id=None,
            subject=self.subject,
            action="read_file",
            resource=SDKComponentRef(component_id="doc_123", component_type="file"),
            data_labels=[],
            metadata={"cluster": "us-east"},
        )

        backend_req = convert_sdk_request_to_backend(sdk_req)

        self.assertEqual(backend_req.trace_id, str(self.flow_id))
        self.assertNotIn("correlation_id", backend_req.context)
        self.assertEqual(backend_req.resource, "file://doc_123")
        self.assertEqual(backend_req.context["data_labels"], [])

    def test_empty_data_labels(self) -> None:
        """Empty data labels are preserved as an empty list in context."""
        sdk_req = SDKAuthorizationRequest(
            flow_id=self.flow_id,
            subject=self.subject,
            action="health_ping",
            resource=SDKComponentRef(component_id="srv_status", component_type="api"),
            data_labels=[],
        )

        backend_req = convert_sdk_request_to_backend(sdk_req)
        self.assertEqual(backend_req.context["data_labels"], [])

    def test_resource_normalization(self) -> None:
        """Resource ComponentRef normalizes deterministically to standard URI scheme."""
        test_cases = [
            ("tool", "stripe_refund", "tool://stripe_refund"),
            ("database", "postgres_replica_01", "database://postgres_replica_01"),
            ("api", "sendgrid_mail_v3", "api://sendgrid_mail_v3"),
            ("model", "gpt-4o", "model://gpt-4o"),
        ]
        for comp_type, comp_id, expected_uri in test_cases:
            ref = SDKComponentRef(component_id=comp_id, component_type=comp_type)
            self.assertEqual(normalize_resource_ref(ref), expected_uri)

    def test_approval_id_preservation(self) -> None:
        """Approval ID in SDK metadata is preserved in context for approval workflow resumption."""
        sdk_req = SDKAuthorizationRequest(
            flow_id=self.flow_id,
            subject=self.subject,
            action="deploy_schema",
            resource=self.resource,
            metadata={"approval_id": "appr_7c4f1a2e9b01"},
        )

        backend_req = convert_sdk_request_to_backend(sdk_req)
        self.assertEqual(backend_req.context.get("approval_id"), "appr_7c4f1a2e9b01")

    def test_invalid_empty_action_rejected(self) -> None:
        """Action cannot be empty or whitespace."""
        with self.assertRaises(ValidationError):
            SDKAuthorizationRequest(
                flow_id=self.flow_id,
                subject=self.subject,
                action="   ",
                resource=self.resource,
            )

    def test_invalid_component_ref_rejected(self) -> None:
        """ComponentRef with empty id or type is rejected."""
        with self.assertRaises(ValidationError):
            SDKComponentRef(component_id="", component_type="tool")

        with self.assertRaises(ValidationError):
            SDKComponentRef(component_id="tool_1", component_type="")

    def test_extra_fields_forbidden(self) -> None:
        """Unknown fields on SDK request are strictly forbidden (extra='forbid')."""
        with self.assertRaises(ValidationError):
            SDKAuthorizationRequest.model_validate(
                {
                    "flow_id": str(self.flow_id),
                    "subject": self.subject.model_dump(),
                    "action": "execute",
                    "resource": self.resource.model_dump(),
                    "unknown_field": "unauthorized",
                }
            )


class TestSDKOutcomeMapping(unittest.TestCase):
    """Verifies safe mapping of internal PolicyEffect to SDK outcome strings."""

    def test_outcome_mapping_allow(self) -> None:
        self.assertEqual(map_policy_effect_to_sdk_outcome(PolicyEffect.ALLOW), "allow")

    def test_outcome_mapping_block(self) -> None:
        self.assertEqual(map_policy_effect_to_sdk_outcome(PolicyEffect.BLOCK), "deny")

    def test_outcome_mapping_require_approval(self) -> None:
        self.assertEqual(
            map_policy_effect_to_sdk_outcome(PolicyEffect.REQUIRE_APPROVAL), "review"
        )

    def test_unknown_effect_fails_closed(self) -> None:
        """Unrecognized effect must fail closed and raise ValueError; never return 'allow'."""
        with self.assertRaises(ValueError):
            map_policy_effect_to_sdk_outcome("UNKNOWN_EFFECT")  # type: ignore[arg-type]


class TestAdapterEvaluatorIntegration(unittest.TestCase):
    """Verifies that converted SDK requests evaluate seamlessly against the Phase 2 policy engine."""

    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
        )

        # Policy 1: Allows file reading
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_allow_read",
                name="Allow read queries",
                priority=10,
                rules=[
                    RuleDefinition(
                        id="rule_read",
                        name="Read rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="read_file",
                        ),
                        effect=PolicyEffect.ALLOW,
                    )
                ],
            )
        )

        # Policy 2: Requires approval for db modification
        self.policy_repo.create_policy(
            PolicyCreate(
                id="pol_db_mod",
                name="Require approval for database modifications",
                priority=50,
                rules=[
                    RuleDefinition(
                        id="rule_modify",
                        name="DB modify rule",
                        condition=ConditionRule(
                            field="action",
                            operator=ConditionOperator.EQ,
                            value="delete_records",
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
                priority=100,
                rules=[
                    RuleDefinition(
                        id="rule_shell",
                        name="Shell execution rule",
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

    def test_sdk_request_evaluates_to_allow(self) -> None:
        """SDK request for read_file converts and evaluates to ALLOW ('allow')."""
        sdk_req = SDKAuthorizationRequest(
            flow_id=uuid4(),
            subject=SDKComponentRef(component_id="agent_reader", component_type="agent"),
            action="read_file",
            resource=SDKComponentRef(component_id="doc_99", component_type="file"),
        )
        backend_req = convert_sdk_request_to_backend(sdk_req)
        decision, appr = self.auth_service.authorize(backend_req)

        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(map_policy_effect_to_sdk_outcome(decision.decision), "allow")
        self.assertEqual(decision.policy_id, "pol_allow_read")
        self.assertIsNone(appr)

    def test_sdk_request_evaluates_to_block(self) -> None:
        """SDK request for exec_shell converts and evaluates to BLOCK ('deny')."""
        sdk_req = SDKAuthorizationRequest(
            flow_id=uuid4(),
            subject=SDKComponentRef(component_id="agent_bad", component_type="agent"),
            action="exec_shell",
            resource=SDKComponentRef(component_id="sh", component_type="tool"),
        )
        backend_req = convert_sdk_request_to_backend(sdk_req)
        decision, appr = self.auth_service.authorize(backend_req)

        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(map_policy_effect_to_sdk_outcome(decision.decision), "deny")
        self.assertEqual(decision.policy_id, "pol_block_shell")
        self.assertIsNone(appr)

    def test_sdk_request_evaluates_to_require_approval(self) -> None:
        """SDK request for delete_records creates ApprovalRequest and maps to 'review'."""
        sdk_req = SDKAuthorizationRequest(
            flow_id=uuid4(),
            subject=SDKComponentRef(component_id="agent_cleanup", component_type="agent"),
            action="delete_records",
            resource=SDKComponentRef(component_id="vault", component_type="database"),
        )
        backend_req = convert_sdk_request_to_backend(sdk_req)
        decision, appr = self.auth_service.authorize(backend_req)

        self.assertEqual(decision.decision, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(map_policy_effect_to_sdk_outcome(decision.decision), "review")
        self.assertIsNotNone(appr)
        self.assertTrue(appr.id.startswith("appr_"))


class TestSDKDecisionAdapter(unittest.TestCase):
    """Verifies typed conversion of canonical AuthorizationDecision to SDK response contract."""

    def test_convert_decision_with_string_policy_slugs(self) -> None:
        """String policy IDs are preserved faithfully in SDKAuthorizationDecision without synthetic UUIDs."""
        req_id = uuid4()
        dec = AuthorizationDecision(
            decision=PolicyEffect.ALLOW,
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Access permitted by policy rules.",
            matched_policy_ids=["pol_read_allow", "pol_compliance_check"],
        )

        sdk_dec = convert_backend_decision_to_sdk(dec, request_id=req_id)

        self.assertEqual(sdk_dec.outcome, "allow")
        self.assertEqual(sdk_dec.request_id, req_id)
        self.assertEqual(sdk_dec.policy_ids, ["pol_read_allow", "pol_compliance_check"])
        self.assertEqual(sdk_dec.reason, "Access permitted by policy rules.")
        self.assertIsInstance(sdk_dec.decision_id, UUID)

        # Direct validation with amgr_sdk model
        try:
            from amgr_sdk.enforcement.client import AuthorizationDecision as SDKClientDecision
        except ModuleNotFoundError:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk"))
            from amgr_sdk.enforcement.client import AuthorizationDecision as SDKClientDecision

        client_model = SDKClientDecision.model_validate(sdk_dec.model_dump(mode="json"))
        self.assertEqual(client_model.outcome, "allow")
        self.assertEqual(client_model.policy_ids, ["pol_read_allow", "pol_compliance_check"])

    def test_convert_decision_with_single_policy_id(self) -> None:
        """Single policy_id fallback is serialized into policy_ids list."""
        req_id = uuid4()
        dec = AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Access blocked.",
            policy_id="pol_block_exec",
        )

        sdk_dec = convert_backend_decision_to_sdk(dec, request_id=req_id)

        self.assertEqual(sdk_dec.outcome, "deny")
        self.assertEqual(sdk_dec.policy_ids, ["pol_block_exec"])

    def test_convert_decision_empty_policy_ids(self) -> None:
        """Decisions with no policy IDs produce an empty list."""
        req_id = uuid4()
        dec = AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            reason_code=ReasonCode.DEFAULT_FALLBACK,
            reason_message="No matching rule.",
        )

        sdk_dec = convert_backend_decision_to_sdk(dec, request_id=req_id)

        self.assertEqual(sdk_dec.outcome, "deny")
        self.assertEqual(sdk_dec.policy_ids, [])

    def test_convert_decision_review_outcome(self) -> None:
        """REQUIRE_APPROVAL effect maps to 'review' outcome."""
        req_id = uuid4()
        dec = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Approval required for sensitive operation.",
            policy_id="pol_require_approval",
        )

        sdk_dec = convert_backend_decision_to_sdk(dec, request_id=req_id)

        self.assertEqual(sdk_dec.outcome, "review")
        self.assertEqual(sdk_dec.policy_ids, ["pol_require_approval"])


if __name__ == "__main__":
    unittest.main()
