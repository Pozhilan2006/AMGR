"""Unit tests for AMGR deterministic policy evaluator and runtime authorization contracts.

Validates AuthorizationRequest, AuthorizationDecision, ReasonCode,
condition evaluation against all operators, scoping filters, fail-closed semantics for
missing security context, disabled policy/rule handling, and rule resolution algorithms
(FIRST_APPLICABLE, BLOCK_OVERRIDES, ALLOW_OVERRIDES).
"""

import unittest
from datetime import datetime, timezone

from backend.app.policy_engine.evaluator import (
    evaluate_condition,
    evaluate_policies,
    evaluate_policy,
    evaluate_rule,
)
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
from backend.app.schemas.policy import PolicyCreate, RuleDefinition
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)

try:
    from pydantic import ValidationError
except ModuleNotFoundError:
    ValidationError = Exception  # type: ignore[assignment, misc]


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestAuthorizationContracts(unittest.TestCase):
    """Verifies schema validation and behaviors for AuthorizationRequest and AuthorizationDecision."""

    def test_valid_authorization_request(self) -> None:
        req = AuthorizationRequest(
            trace_id="trc_001",
            subject={"user_id": "usr_alice", "role": "engineer", "clearance": 2},
            action="execute_tool",
            resource="tool_terminal_exec",
            context={"environment": "production", "network_zone": "internal"},
        )
        self.assertEqual(req.trace_id, "trc_001")
        self.assertEqual(req.action, "execute_tool")
        self.assertEqual(req.resource, "tool_terminal_exec")
        self.assertEqual(req.subject["role"], "engineer")  # type: ignore[index]

    def test_authorization_request_immutability(self) -> None:
        req = AuthorizationRequest(
            trace_id="trc_002",
            subject="agent_sub_01",
            action="read_file",
            resource="config.yaml",
        )
        with self.assertRaises(ValidationError):
            req.action = "delete_file"  # type: ignore[misc]

    def test_authorization_request_empty_fields_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AuthorizationRequest(
                trace_id="   ",
                subject="agent_01",
                action="read",
                resource="res_1",
            )
        with self.assertRaises(ValidationError):
            AuthorizationRequest(
                trace_id="trc_003",
                subject="agent_01",
                action="",
                resource="res_1",
            )
        with self.assertRaises(ValidationError):
            AuthorizationRequest(
                trace_id="trc_003",
                subject="agent_01",
                action="read",
                resource="  ",
            )

    def test_authorization_request_extra_fields_forbidden(self) -> None:
        with self.assertRaises(ValidationError):
            AuthorizationRequest(
                trace_id="trc_004",
                subject="agent_01",
                action="read",
                resource="res_1",
                unknown_extra_param="illegal",  # type: ignore[call-arg]
            )

    def test_authorization_request_to_eval_context(self) -> None:
        req = AuthorizationRequest(
            trace_id="trc_005",
            subject={"role": "admin", "department": "security"},
            action="deploy_service",
            resource="service_gateway",
            context={"env": "prod", "cluster": "us-east-1"},
        )
        ctx = req.to_eval_context()
        self.assertEqual(ctx["action"], "deploy_service")
        self.assertEqual(ctx["resource"], "service_gateway")
        self.assertEqual(ctx["trace_id"], "trc_005")
        self.assertEqual(ctx["subject"]["role"], "admin")
        self.assertEqual(ctx["env"], "prod")
        self.assertEqual(ctx["cluster"], "us-east-1")

    def test_valid_authorization_decision(self) -> None:
        now = datetime.now(timezone.utc)
        decision = AuthorizationDecision(
            decision=PolicyEffect.ALLOW,
            policy_id="pol_gateway",
            matched_rule_id="rule_allow_admin",
            matched_rule_ids=["rule_allow_admin"],
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Rule 'rule_allow_admin' matched.",
            evaluated_at=now,
        )
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_gateway")
        self.assertEqual(decision.matched_rule_id, "rule_allow_admin")
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)


class TestDeterministicConditionEvaluation(unittest.TestCase):
    """Verifies deterministic evaluation of ConditionRule and ConditionGroup against attributes."""

    def test_equality_and_inequality_operators(self) -> None:
        ctx = {"env": "staging", "retries": 3, "active": True}

        # EQ
        rule_eq = ConditionRule(field="env", operator=ConditionOperator.EQ, value="staging")
        self.assertTrue(evaluate_condition(rule_eq, ctx))
        rule_eq_fail = ConditionRule(field="env", operator=ConditionOperator.EQ, value="prod")
        self.assertFalse(evaluate_condition(rule_eq_fail, ctx))

        # NOT_EQ
        rule_neq = ConditionRule(field="env", operator=ConditionOperator.NOT_EQ, value="prod")
        self.assertTrue(evaluate_condition(rule_neq, ctx))
        rule_neq_fail = ConditionRule(field="env", operator=ConditionOperator.NOT_EQ, value="staging")
        self.assertFalse(evaluate_condition(rule_neq_fail, ctx))

    def test_membership_operators(self) -> None:
        ctx = {"role": "analyst", "tier": "gold"}

        rule_in = ConditionRule(field="role", operator=ConditionOperator.IN, value=["analyst", "engineer"])
        self.assertTrue(evaluate_condition(rule_in, ctx))

        rule_in_fail = ConditionRule(field="role", operator=ConditionOperator.IN, value=["admin", "root"])
        self.assertFalse(evaluate_condition(rule_in_fail, ctx))

        rule_not_in = ConditionRule(field="role", operator=ConditionOperator.NOT_IN, value=["guest", "anonymous"])
        self.assertTrue(evaluate_condition(rule_not_in, ctx))

        rule_not_in_fail = ConditionRule(field="role", operator=ConditionOperator.NOT_IN, value=["analyst"])
        self.assertFalse(evaluate_condition(rule_not_in_fail, ctx))

    def test_string_matching_operators(self) -> None:
        ctx = {"path": "/var/log/audit.log", "service": "payment-api-gateway"}

        # CONTAINS
        self.assertTrue(evaluate_condition(
            ConditionRule(field="path", operator=ConditionOperator.CONTAINS, value="audit"), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="path", operator=ConditionOperator.CONTAINS, value="secret"), ctx
        ))

        # STARTS_WITH
        self.assertTrue(evaluate_condition(
            ConditionRule(field="service", operator=ConditionOperator.STARTS_WITH, value="payment-"), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="service", operator=ConditionOperator.STARTS_WITH, value="auth-"), ctx
        ))

        # ENDS_WITH
        self.assertTrue(evaluate_condition(
            ConditionRule(field="path", operator=ConditionOperator.ENDS_WITH, value=".log"), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="path", operator=ConditionOperator.ENDS_WITH, value=".json"), ctx
        ))

    def test_relational_numeric_operators_and_type_guards(self) -> None:
        ctx = {"score": 85, "limit": 100.5, "flag": True}

        # GT / GTE / LT / LTE
        self.assertTrue(evaluate_condition(
            ConditionRule(field="score", operator=ConditionOperator.GT, value=80), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="score", operator=ConditionOperator.GT, value=85), ctx
        ))
        self.assertTrue(evaluate_condition(
            ConditionRule(field="score", operator=ConditionOperator.GTE, value=85), ctx
        ))
        self.assertTrue(evaluate_condition(
            ConditionRule(field="score", operator=ConditionOperator.LT, value=90), ctx
        ))
        self.assertTrue(evaluate_condition(
            ConditionRule(field="score", operator=ConditionOperator.LTE, value=85), ctx
        ))

        # Strict boolean rejection (in Python, isinstance(True, int) is True, but booleans must not be compared numerically)
        self.assertFalse(evaluate_condition(
            ConditionRule(field="flag", operator=ConditionOperator.GT, value=0), ctx
        ))

    def test_existence_operators(self) -> None:
        ctx = {"present_key": "val", "nested": {"child": 42}}

        self.assertTrue(evaluate_condition(
            ConditionRule(field="present_key", operator=ConditionOperator.EXISTS, value=None), ctx
        ))
        self.assertTrue(evaluate_condition(
            ConditionRule(field="nested.child", operator=ConditionOperator.EXISTS, value=None), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="missing_key", operator=ConditionOperator.EXISTS, value=None), ctx
        ))

        self.assertTrue(evaluate_condition(
            ConditionRule(field="missing_key", operator=ConditionOperator.NOT_EXISTS, value=None), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="present_key", operator=ConditionOperator.NOT_EXISTS, value=None), ctx
        ))

    def test_missing_context_fails_closed_never_silently_matches(self) -> None:
        """Deterministic safety rule: Missing context attributes must NEVER evaluate to True for positive checks."""
        ctx = {"unrelated_attribute": "value"}

        # Attempting positive equality or membership against nonexistent security context
        self.assertFalse(evaluate_condition(
            ConditionRule(field="subject.role", operator=ConditionOperator.EQ, value="admin"), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="subject.security_clearance", operator=ConditionOperator.GTE, value=5), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="subject.scopes", operator=ConditionOperator.IN, value=["admin", "superadmin"]), ctx
        ))
        self.assertFalse(evaluate_condition(
            ConditionRule(field="subject.department", operator=ConditionOperator.CONTAINS, value="finance"), ctx
        ))

    def test_compound_condition_groups(self) -> None:
        ctx = {"role": "operator", "level": 3, "location": "datacenter_a"}

        # all_of: matches
        cg_all = ConditionGroup(all_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="operator"),
            ConditionRule(field="level", operator=ConditionOperator.GTE, value=2),
        ])
        self.assertTrue(evaluate_condition(cg_all, ctx))

        # all_of: one condition fails
        cg_all_fail = ConditionGroup(all_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="operator"),
            ConditionRule(field="level", operator=ConditionOperator.GT, value=5),
        ])
        self.assertFalse(evaluate_condition(cg_all_fail, ctx))

        # any_of: matches
        cg_any = ConditionGroup(any_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="admin"),
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="operator"),
        ])
        self.assertTrue(evaluate_condition(cg_any, ctx))

        # any_of: fails
        cg_any_fail = ConditionGroup(any_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="admin"),
            ConditionRule(field="level", operator=ConditionOperator.GTE, value=10),
        ])
        self.assertFalse(evaluate_condition(cg_any_fail, ctx))

        # none_of: matches
        cg_none = ConditionGroup(none_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="banned"),
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="guest"),
        ])
        self.assertTrue(evaluate_condition(cg_none, ctx))

        # none_of: fails because forbidden rule matches
        cg_none_fail = ConditionGroup(none_of=[
            ConditionRule(field="role", operator=ConditionOperator.EQ, value="operator"),
        ])
        self.assertFalse(evaluate_condition(cg_none_fail, ctx))


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestRuleAndPolicyEvaluation(unittest.TestCase):
    """Verifies rule evaluation and resolution algorithms across multi-rule policies."""

    def test_rule_scoping_action_and_resource_filters(self) -> None:
        rule = RuleDefinition(
            id="rule_scoped",
            name="Scoped Rule",
            description="Matches only specific action and resource",
            effect=PolicyEffect.ALLOW,
            priority=10,
            target_actions=["execute_sql", "execute_script"],
            target_resources=["db_primary", "db_replica"],
            condition=ConditionRule(field="subject.role", operator=ConditionOperator.EQ, value="dba"),
        )

        req_match = AuthorizationRequest(
            trace_id="trc_101",
            subject={"role": "dba"},
            action="execute_sql",
            resource="db_primary",
        )
        self.assertTrue(evaluate_rule(rule, req_match))

        # Action mismatch
        req_wrong_action = AuthorizationRequest(
            trace_id="trc_102",
            subject={"role": "dba"},
            action="drop_table",
            resource="db_primary",
        )
        self.assertFalse(evaluate_rule(rule, req_wrong_action))

        # Resource mismatch
        req_wrong_resource = AuthorizationRequest(
            trace_id="trc_103",
            subject={"role": "dba"},
            action="execute_sql",
            resource="s3_bucket",
        )
        self.assertFalse(evaluate_rule(rule, req_wrong_resource))

    def test_disabled_rule_never_evaluates_to_true(self) -> None:
        rule = RuleDefinition(
            id="rule_disabled",
            name="Disabled Rule",
            description="Inactive rule",
            effect=PolicyEffect.ALLOW,
            priority=1,
            enabled=False,
            condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="ping"),
        )
        req = AuthorizationRequest(
            trace_id="trc_104",
            subject="tester",
            action="ping",
            resource="gateway",
        )
        self.assertFalse(evaluate_rule(rule, req))

    def test_disabled_policy_returns_default_effect(self) -> None:
        policy = PolicyCreate(
            id="pol_disabled",
            name="Disabled Policy",
            description="Policy currently switched off",
            enabled=False,
            default_effect=PolicyEffect.BLOCK,
            rules=[
                RuleDefinition(
                    id="rule_allow_all",
                    name="Allow All",
                    description="Should not trigger",
                    effect=PolicyEffect.ALLOW,
                    priority=1,
                    condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="run"),
                )
            ],
        )
        req = AuthorizationRequest(
            trace_id="trc_105",
            subject="admin",
            action="run",
            resource="server_01",
        )
        decision = evaluate_policy(policy, req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.POLICY_DISABLED)
        self.assertIsNone(decision.matched_rule_id)
        self.assertEqual(decision.matched_rule_ids, [])

    def test_first_applicable_priority_resolution(self) -> None:
        """Verifies FIRST_APPLICABLE picks the highest priority (highest numerical value) matching rule."""
        rule_high = RuleDefinition(
            id="rule_block_untrusted",
            name="Block Untrusted",
            description="Blocks untrusted requests first",
            effect=PolicyEffect.BLOCK,
            priority=100,
            condition=ConditionRule(field="env", operator=ConditionOperator.EQ, value="untrusted"),
        )
        rule_low = RuleDefinition(
            id="rule_allow_ops",
            name="Allow Operations",
            description="Allows ops staff",
            effect=PolicyEffect.ALLOW,
            priority=10,
            condition=ConditionRule(field="role", operator=ConditionOperator.EQ, value="ops"),
        )

        policy = PolicyCreate(
            id="pol_priority",
            name="Priority Policy",
            description="Tests FIRST_APPLICABLE priority ordering",
            resolution_algorithm=RuleResolutionAlgorithm.FIRST_APPLICABLE,
            default_effect=PolicyEffect.BLOCK,
            rules=[rule_low, rule_high],  # Defined in reverse order
        )

        # Both conditions match: priority 100 (BLOCK) must win over priority 10 (ALLOW)
        req_both_match = AuthorizationRequest(
            trace_id="trc_106",
            subject="operator",
            action="deploy",
            resource="cluster",
            context={"env": "untrusted", "role": "ops"},
        )
        decision = evaluate_policy(policy, req_both_match)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.matched_rule_id, "rule_block_untrusted")
        self.assertEqual(decision.matched_rule_ids, ["rule_block_untrusted"])
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)

        # Only priority 10 matches: returns ALLOW
        req_ops_only = AuthorizationRequest(
            trace_id="trc_107",
            subject="operator",
            action="deploy",
            resource="cluster",
            context={"env": "trusted", "role": "ops"},
        )
        decision_ops = evaluate_policy(policy, req_ops_only)
        self.assertEqual(decision_ops.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision_ops.matched_rule_id, "rule_allow_ops")

        # Neither matches: fallback to default_effect (BLOCK)
        req_no_match = AuthorizationRequest(
            trace_id="trc_108",
            subject="guest",
            action="deploy",
            resource="cluster",
            context={"env": "trusted", "role": "visitor"},
        )
        decision_fallback = evaluate_policy(policy, req_no_match)
        self.assertEqual(decision_fallback.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision_fallback.reason_code, ReasonCode.DEFAULT_FALLBACK)
        self.assertIsNone(decision_fallback.matched_rule_id)

    def test_block_overrides_resolution_algorithm(self) -> None:
        """In BLOCK_OVERRIDES, if any matching rule has effect BLOCK, the final outcome is BLOCK."""
        rule_allow = RuleDefinition(
            id="rule_allow_regular",
            name="Allow Regular Access",
            description="Allows standard operations",
            effect=PolicyEffect.ALLOW,
            priority=1,
            condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="read_db"),
        )
        rule_block = RuleDefinition(
            id="rule_block_maintenance",
            name="Block Maintenance Window",
            description="Blocks all operations during maintenance",
            effect=PolicyEffect.BLOCK,
            priority=2,
            condition=ConditionRule(field="maintenance", operator=ConditionOperator.EQ, value=True),
        )

        policy = PolicyCreate(
            id="pol_block_overrides",
            name="Block Overrides Policy",
            description="Tests BLOCK_OVERRIDES algorithm",
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            rules=[rule_allow, rule_block],
        )

        # Both match: even though ALLOW has priority 1, BLOCK overrides!
        req_during_maint = AuthorizationRequest(
            trace_id="trc_109",
            subject="analyst",
            action="read_db",
            resource="postgres_main",
            context={"maintenance": True},
        )
        decision = evaluate_policy(policy, req_during_maint)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.matched_rule_id, "rule_block_maintenance")
        self.assertIn("rule_allow_regular", decision.matched_rule_ids)
        self.assertIn("rule_block_maintenance", decision.matched_rule_ids)

        # Outside maintenance: only ALLOW matches
        req_normal = AuthorizationRequest(
            trace_id="trc_110",
            subject="analyst",
            action="read_db",
            resource="postgres_main",
            context={"maintenance": False},
        )
        decision_normal = evaluate_policy(policy, req_normal)
        self.assertEqual(decision_normal.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision_normal.matched_rule_id, "rule_allow_regular")

    def test_allow_overrides_resolution_algorithm(self) -> None:
        """In ALLOW_OVERRIDES, if any matching rule has effect ALLOW, the final outcome is ALLOW."""
        rule_block_general = RuleDefinition(
            id="rule_block_general",
            name="Block General Access",
            description="Broad block rule",
            effect=PolicyEffect.BLOCK,
            priority=1,
            condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="access_internal"),
        )
        rule_allow_emergency = RuleDefinition(
            id="rule_allow_emergency",
            name="Allow Emergency Bypass",
            description="Emergency override allowance",
            effect=PolicyEffect.ALLOW,
            priority=5,
            condition=ConditionRule(field="emergency_mode", operator=ConditionOperator.EQ, value=True),
        )

        policy = PolicyCreate(
            id="pol_allow_overrides",
            name="Allow Overrides Policy",
            description="Tests ALLOW_OVERRIDES algorithm",
            resolution_algorithm=RuleResolutionAlgorithm.ALLOW_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            rules=[rule_block_general, rule_allow_emergency],
        )

        # Both match: ALLOW overrides BLOCK
        req_emergency = AuthorizationRequest(
            trace_id="trc_111",
            subject="oncall",
            action="access_internal",
            resource="core_cluster",
            context={"emergency_mode": True},
        )
        decision = evaluate_policy(policy, req_emergency)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.matched_rule_id, "rule_allow_emergency")
        self.assertIn("rule_block_general", decision.matched_rule_ids)
        self.assertIn("rule_allow_emergency", decision.matched_rule_ids)

    def test_missing_security_context_fails_closed_to_block(self) -> None:
        """Critical security test: Missing attributes must never evaluate to ALLOW.

        When a policy requires caller clearance or role, and the request lacks it,
        the rule must NOT match and the fallback must evaluate to BLOCK.
        """
        rule_allow_privileged = RuleDefinition(
            id="rule_allow_vip",
            name="Allow Privileged Clearance",
            description="Grants access only to users with clearance >= 5",
            effect=PolicyEffect.ALLOW,
            priority=1,
            condition=ConditionRule(
                field="subject.clearance_level",
                operator=ConditionOperator.GTE,
                value=5,
            ),
        )

        policy = PolicyCreate(
            id="pol_secure_enclave",
            name="Secure Enclave Policy",
            description="Requires strict clearance",
            default_effect=PolicyEffect.BLOCK,
            rules=[rule_allow_privileged],
        )

        # Request missing 'clearance_level' inside subject
        req_missing_clearance = AuthorizationRequest(
            trace_id="trc_112",
            subject={"username": "attacker_or_unauthenticated"},
            action="access_enclave",
            resource="vault_secrets",
        )
        decision = evaluate_policy(policy, req_missing_clearance)

        # MUST be BLOCK via MISSING_CONTEXT
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.MISSING_CONTEXT)
        self.assertIsNone(decision.matched_rule_id)
        self.assertEqual(decision.matched_rule_ids, [])


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyPriorityAndConflictResolution(unittest.TestCase):
    """Verifies policy priority ordering and conflict-resolution across multiple policies."""

    def _make_simple_policy(
        self,
        policy_id: str,
        priority: int,
        effect: PolicyEffect,
        condition_field: str = "action",
        condition_val: str = "exec",
        enabled: bool = True,
    ) -> PolicyCreate:
        rule = RuleDefinition(
            id=f"rule_{policy_id}",
            name=f"Rule for {policy_id}",
            effect=effect,
            priority=1,
            condition=ConditionRule(
                field=condition_field,
                operator=ConditionOperator.EQ,
                value=condition_val,
            ),
        )
        return PolicyCreate(
            id=policy_id,
            name=f"Policy {policy_id}",
            priority=priority,
            enabled=enabled,
            default_effect=PolicyEffect.BLOCK,
            rules=[rule],
        )

    def test_higher_priority_policy_overrides_lower_priority(self) -> None:
        """Higher-priority policy strictly wins over lower-priority policy."""
        pol_high = self._make_simple_policy("pol_high_allow", priority=100, effect=PolicyEffect.ALLOW)
        pol_low = self._make_simple_policy("pol_low_block", priority=10, effect=PolicyEffect.BLOCK)

        req = AuthorizationRequest(
            trace_id="trc_201",
            subject="agent_alpha",
            action="exec",
            resource="tool_calc",
        )

        decision = evaluate_policies([pol_low, pol_high], req)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_high_allow")
        self.assertEqual(decision.matched_policy_ids, ["pol_high_allow"])
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)

    def test_lower_priority_policy_applies_when_higher_does_not_match(self) -> None:
        """When a high-priority policy does not match, the next applicable policy applies."""
        pol_vip = self._make_simple_policy("pol_vip", priority=100, effect=PolicyEffect.ALLOW, condition_val="vip_action")
        pol_std = self._make_simple_policy("pol_std", priority=20, effect=PolicyEffect.REQUIRE_APPROVAL, condition_val="std_action")

        req = AuthorizationRequest(
            trace_id="trc_202",
            subject="user_bob",
            action="std_action",
            resource="service_api",
        )

        decision = evaluate_policies([pol_vip, pol_std], req)
        self.assertEqual(decision.decision, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(decision.policy_id, "pol_std")
        self.assertEqual(decision.matched_policy_ids, ["pol_std"])

    def test_equal_priority_unanimous_decision(self) -> None:
        """Multiple policies at equal priority with the same effect render unanimous decision."""
        pol_a = self._make_simple_policy("pol_zebra", priority=50, effect=PolicyEffect.ALLOW)
        pol_b = self._make_simple_policy("pol_apple", priority=50, effect=PolicyEffect.ALLOW)

        req = AuthorizationRequest(
            trace_id="trc_203",
            subject="operator",
            action="exec",
            resource="terminal",
        )

        decision = evaluate_policies([pol_a, pol_b], req)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        # Deterministic tie-breaker selects pol_apple as primary because 'pol_apple' < 'pol_zebra'
        self.assertEqual(decision.policy_id, "pol_apple")
        self.assertEqual(decision.matched_policy_ids, ["pol_apple", "pol_zebra"])
        self.assertEqual(decision.reason_code, ReasonCode.RULE_MATCHED)

    def test_equal_priority_conflict_block_overrides_allow(self) -> None:
        """Under default BLOCK_OVERRIDES, conflicting BLOCK overrides ALLOW at equal priority."""
        pol_allow = self._make_simple_policy("pol_allow", priority=50, effect=PolicyEffect.ALLOW)
        pol_block = self._make_simple_policy("pol_block", priority=50, effect=PolicyEffect.BLOCK)

        req = AuthorizationRequest(
            trace_id="trc_204",
            subject="user",
            action="exec",
            resource="tool",
        )

        decision = evaluate_policies([pol_allow, pol_block], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.policy_id, "pol_block")
        self.assertEqual(decision.reason_code, ReasonCode.CONFLICT_RESOLVED)
        self.assertIn("pol_allow", decision.matched_policy_ids)
        self.assertIn("pol_block", decision.matched_policy_ids)
        self.assertIn("BLOCK_OVERRIDES", decision.reason_message)

    def test_equal_priority_conflict_require_approval_overrides_allow(self) -> None:
        """Under BLOCK_OVERRIDES, REQUIRE_APPROVAL overrides ALLOW."""
        pol_allow = self._make_simple_policy("pol_allow", priority=50, effect=PolicyEffect.ALLOW)
        pol_appr = self._make_simple_policy("pol_approval", priority=50, effect=PolicyEffect.REQUIRE_APPROVAL)

        req = AuthorizationRequest(
            trace_id="trc_205",
            subject="user",
            action="exec",
            resource="tool",
        )

        decision = evaluate_policies([pol_allow, pol_appr], req)
        self.assertEqual(decision.decision, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(decision.policy_id, "pol_approval")
        self.assertEqual(decision.reason_code, ReasonCode.CONFLICT_RESOLVED)

    def test_equal_priority_conflict_block_overrides_require_approval(self) -> None:
        """Under BLOCK_OVERRIDES, BLOCK overrides REQUIRE_APPROVAL."""
        pol_appr = self._make_simple_policy("pol_approval", priority=50, effect=PolicyEffect.REQUIRE_APPROVAL)
        pol_block = self._make_simple_policy("pol_block", priority=50, effect=PolicyEffect.BLOCK)

        req = AuthorizationRequest(
            trace_id="trc_206",
            subject="user",
            action="exec",
            resource="tool",
        )

        decision = evaluate_policies([pol_appr, pol_block], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.policy_id, "pol_block")
        self.assertEqual(decision.reason_code, ReasonCode.CONFLICT_RESOLVED)

    def test_equal_priority_conflict_allow_overrides_algorithm(self) -> None:
        """Under ALLOW_OVERRIDES, ALLOW overrides BLOCK."""
        pol_allow = self._make_simple_policy("pol_allow", priority=50, effect=PolicyEffect.ALLOW)
        pol_block = self._make_simple_policy("pol_block", priority=50, effect=PolicyEffect.BLOCK)

        req = AuthorizationRequest(
            trace_id="trc_207",
            subject="user",
            action="exec",
            resource="tool",
        )

        decision = evaluate_policies(
            [pol_allow, pol_block],
            req,
            conflict_algorithm=RuleResolutionAlgorithm.ALLOW_OVERRIDES,
        )
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_allow")
        self.assertEqual(decision.reason_code, ReasonCode.CONFLICT_RESOLVED)

    def test_equal_priority_conflict_first_applicable_algorithm(self) -> None:
        """Under FIRST_APPLICABLE, deterministic policy ID ordering tie-breaks the decision."""
        pol_allow = self._make_simple_policy("pol_aaa", priority=50, effect=PolicyEffect.ALLOW)
        pol_block = self._make_simple_policy("pol_zzz", priority=50, effect=PolicyEffect.BLOCK)

        req = AuthorizationRequest(
            trace_id="trc_208",
            subject="user",
            action="exec",
            resource="tool",
        )

        decision = evaluate_policies(
            [pol_block, pol_allow],  # Given in reverse order
            req,
            conflict_algorithm=RuleResolutionAlgorithm.FIRST_APPLICABLE,
        )
        # 'pol_aaa' comes first alphabetically, so its ALLOW wins
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_aaa")
        self.assertEqual(decision.reason_code, ReasonCode.CONFLICT_RESOLVED)

    def test_no_policies_match_falls_back_to_default_block(self) -> None:
        pol = self._make_simple_policy("pol_nomatch", priority=10, effect=PolicyEffect.ALLOW, condition_val="other_action")
        req = AuthorizationRequest(
            trace_id="trc_209",
            subject="user",
            action="my_action",
            resource="tool",
        )
        decision = evaluate_policies([pol], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.DEFAULT_FALLBACK)
        self.assertIsNone(decision.policy_id)

    def test_empty_policy_list_falls_back_to_default_block(self) -> None:
        req = AuthorizationRequest(
            trace_id="trc_210",
            subject="user",
            action="exec",
            resource="tool",
        )
        decision = evaluate_policies([], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.DEFAULT_FALLBACK)
        self.assertIsNone(decision.policy_id)

    def test_all_policies_disabled_falls_back_to_disabled_reason(self) -> None:
        pol_dis = self._make_simple_policy("pol_disabled", priority=10, effect=PolicyEffect.ALLOW, enabled=False)
        req = AuthorizationRequest(
            trace_id="trc_211",
            subject="user",
            action="exec",
            resource="tool",
        )
        decision = evaluate_policies([pol_dis], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.POLICY_DISABLED)

    def test_disabled_policy_ignored_when_active_matches(self) -> None:
        pol_dis = self._make_simple_policy("pol_dis_high", priority=100, effect=PolicyEffect.BLOCK, enabled=False)
        pol_act = self._make_simple_policy("pol_act_low", priority=10, effect=PolicyEffect.ALLOW, enabled=True)

        req = AuthorizationRequest(
            trace_id="trc_212",
            subject="user",
            action="exec",
            resource="tool",
        )
        decision = evaluate_policies([pol_dis, pol_act], req)
        self.assertEqual(decision.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision.policy_id, "pol_act_low")

    def test_missing_context_fails_closed_across_policies(self) -> None:
        """Missing security context must NEVER evaluate to ALLOW across multiple policies."""
        pol_clearance = self._make_simple_policy(
            "pol_clearance",
            priority=50,
            effect=PolicyEffect.ALLOW,
            condition_field="subject.clearance_level",
            condition_val="top_secret",
        )
        req = AuthorizationRequest(
            trace_id="trc_213",
            subject={"user": "unauthenticated"},  # Missing clearance_level
            action="exec",
            resource="vault",
        )
        decision = evaluate_policies([pol_clearance], req)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.MISSING_CONTEXT)
        self.assertIsNone(decision.policy_id)


if __name__ == "__main__":
    unittest.main()

