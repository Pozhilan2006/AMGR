"""Unit tests for AMGR policy and rule definition schemas.

Validates RuleDefinition, PolicyCreate, PolicyRead, priority sorting,
uniqueness constraints, and serialization integrity.
"""

import unittest
from datetime import datetime

from backend.app.schemas.condition import (
    PYDANTIC_AVAILABLE,
    ConditionGroup,
    ConditionRule,
)
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    RuleDefinition,
    sort_policies,
)
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
class TestRuleDefinitionValidation(unittest.TestCase):
    """Verifies validation rules on RuleDefinition schemas."""

    def test_valid_atomic_rule(self) -> None:
        condition = ConditionRule(
            field="subject.role",
            operator=ConditionOperator.EQ,
            value="admin",
        )
        rule = RuleDefinition(
            id="rule_allow_admin",
            name="Allow Administrator Access",
            description="Grants access to subjects with admin role",
            condition=condition,
            effect=PolicyEffect.ALLOW,
            priority=100,
            enabled=True,
            target_actions=["execute_tool", "query_db"],
            target_resources=["prod-cluster"],
        )
        self.assertEqual(rule.id, "rule_allow_admin")
        self.assertEqual(rule.name, "Allow Administrator Access")
        self.assertEqual(rule.effect, PolicyEffect.ALLOW)
        self.assertEqual(rule.priority, 100)
        self.assertTrue(rule.enabled)
        self.assertEqual(rule.target_actions, ["execute_tool", "query_db"])
        self.assertEqual(rule.target_resources, ["prod-cluster"])

    def test_valid_compound_rule(self) -> None:
        group = ConditionGroup(
            all_of=[
                ConditionRule(field="subject.clearance", operator=ConditionOperator.GTE, value=3),
                ConditionRule(field="context.mfa_verified", operator=ConditionOperator.EQ, value=True),
            ]
        )
        rule = RuleDefinition(
            id="rule_mfa_check",
            name="Enforce MFA and Clearance",
            condition=group,
            effect=PolicyEffect.REQUIRE_APPROVAL,
            priority=50,
        )
        self.assertEqual(rule.id, "rule_mfa_check")
        self.assertEqual(rule.effect, PolicyEffect.REQUIRE_APPROVAL)
        self.assertEqual(rule.priority, 50)
        self.assertTrue(rule.enabled)
        self.assertIsNone(rule.target_actions)

    def test_reject_invalid_rule_ids(self) -> None:
        condition = ConditionRule(field="user.role", operator=ConditionOperator.EQ, value="guest")
        invalid_ids = [
            "",
            "   ",
            "rule with spaces",
            "rule;drop",
            "rule.dot",
            "__dunder_rule",
            "rule!bad",
        ]
        for rule_id in invalid_ids:
            with self.subTest(rule_id=rule_id):
                with self.assertRaises(ValidationError):
                    RuleDefinition(
                        id=rule_id,
                        name="Test Rule",
                        condition=condition,
                        effect=PolicyEffect.BLOCK,
                    )

    def test_reject_empty_rule_name(self) -> None:
        condition = ConditionRule(field="user.role", operator=ConditionOperator.EQ, value="guest")
        with self.assertRaises(ValidationError):
            RuleDefinition(
                id="rule_1",
                name="   ",
                condition=condition,
                effect=PolicyEffect.BLOCK,
            )

    def test_reject_negative_priority(self) -> None:
        condition = ConditionRule(field="user.role", operator=ConditionOperator.EQ, value="guest")
        with self.assertRaises(ValidationError):
            RuleDefinition(
                id="rule_1",
                name="Test Rule",
                condition=condition,
                effect=PolicyEffect.BLOCK,
                priority=-1,
            )

    def test_reject_extra_fields_on_rule(self) -> None:
        condition = ConditionRule(field="user.role", operator=ConditionOperator.EQ, value="guest")
        with self.assertRaises(ValidationError):
            RuleDefinition.model_validate(
                {
                    "id": "rule_1",
                    "name": "Test Rule",
                    "condition": condition.model_dump(),
                    "effect": "BLOCK",
                    "injected_field": "unauthorized",
                }
            )


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyDefinitionValidation(unittest.TestCase):
    """Verifies validation rules on PolicyCreate and PolicyRead schemas."""

    def _create_sample_rule(self, rule_id: str, priority: int = 0) -> RuleDefinition:
        return RuleDefinition(
            id=rule_id,
            name=f"Rule {rule_id}",
            condition=ConditionRule(field="action.type", operator=ConditionOperator.EQ, value="read"),
            effect=PolicyEffect.ALLOW,
            priority=priority,
        )

    def test_valid_policy_create(self) -> None:
        rules = [
            self._create_sample_rule("rule_1", priority=10),
            self._create_sample_rule("rule_2", priority=20),
        ]
        policy = PolicyCreate(
            id="pol_read_access",
            name="Read Access Policy",
            description="Controls read operations across runtime agents",
            rules=rules,
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
        )
        self.assertEqual(policy.id, "pol_read_access")
        self.assertEqual(policy.name, "Read Access Policy")
        self.assertEqual(len(policy.rules), 2)
        self.assertEqual(policy.resolution_algorithm, RuleResolutionAlgorithm.BLOCK_OVERRIDES)
        self.assertEqual(policy.default_effect, PolicyEffect.BLOCK)
        self.assertTrue(policy.enabled)

    def test_policy_create_defaults(self) -> None:
        rule = self._create_sample_rule("rule_default")
        policy = PolicyCreate(
            id="pol_default",
            name="Default Policy",
            rules=[rule],
        )
        self.assertEqual(policy.resolution_algorithm, RuleResolutionAlgorithm.FIRST_APPLICABLE)
        self.assertEqual(policy.default_effect, PolicyEffect.BLOCK)
        self.assertTrue(policy.enabled)
        self.assertIsNone(policy.description)

    def test_reject_duplicate_rule_ids(self) -> None:
        rules = [
            self._create_sample_rule("duplicate_rule_id", priority=10),
            self._create_sample_rule("duplicate_rule_id", priority=20),
        ]
        with self.assertRaises(ValidationError):
            PolicyCreate(
                id="pol_duplicate",
                name="Duplicate Rule Policy",
                rules=rules,
            )

    def test_reject_empty_rules_list(self) -> None:
        with self.assertRaises(ValidationError):
            PolicyCreate(
                id="pol_empty",
                name="Empty Policy",
                rules=[],
            )

    def test_reject_invalid_policy_id(self) -> None:
        rule = self._create_sample_rule("rule_1")
        invalid_ids = ["", "   ", "pol with spaces", "pol.dot", "__dunder_pol", "pol!"]
        for pol_id in invalid_ids:
            with self.subTest(pol_id=pol_id):
                with self.assertRaises(ValidationError):
                    PolicyCreate(
                        id=pol_id,
                        name="Policy Name",
                        rules=[rule],
                    )

    def test_reject_extra_fields_on_policy(self) -> None:
        rule = self._create_sample_rule("rule_1")
        with self.assertRaises(ValidationError):
            PolicyCreate.model_validate(
                {
                    "id": "pol_extra",
                    "name": "Extra Field Policy",
                    "rules": [rule.model_dump()],
                    "injected_property": "attack",
                }
            )


    def test_policy_priority_default_and_custom(self) -> None:
        rule = self._create_sample_rule("rule_priority")
        policy_default = PolicyCreate(
            id="pol_default_prio",
            name="Default Priority Policy",
            rules=[rule],
        )
        self.assertEqual(policy_default.priority, 0)

        policy_custom = PolicyCreate(
            id="pol_custom_prio",
            name="Custom Priority Policy",
            rules=[rule],
            priority=50,
        )
        self.assertEqual(policy_custom.priority, 50)

    def test_reject_negative_policy_priority(self) -> None:
        rule = self._create_sample_rule("rule_neg_prio")
        with self.assertRaises(ValidationError):
            PolicyCreate(
                id="pol_neg_prio",
                name="Negative Priority Policy",
                rules=[rule],
                priority=-5,
            )


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyPrioritySorting(unittest.TestCase):
    """Verifies deterministic sorting of rules and policies by priority."""

    def _create_sample_rule(self, rule_id: str) -> RuleDefinition:
        return RuleDefinition(
            id=rule_id,
            name="Sample Rule",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value=1),
            effect=PolicyEffect.ALLOW,
        )

    def test_get_sorted_rules(self) -> None:
        rule_low = RuleDefinition(
            id="rule_low",
            name="Low Priority",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value=1),
            effect=PolicyEffect.ALLOW,
            priority=10,
        )
        rule_high = RuleDefinition(
            id="rule_high",
            name="High Priority",
            condition=ConditionRule(field="b", operator=ConditionOperator.EQ, value=2),
            effect=PolicyEffect.BLOCK,
            priority=100,
        )
        rule_mid = RuleDefinition(
            id="rule_mid",
            name="Medium Priority",
            condition=ConditionRule(field="c", operator=ConditionOperator.EQ, value=3),
            effect=PolicyEffect.REQUIRE_APPROVAL,
            priority=50,
        )

        policy = PolicyCreate(
            id="pol_priority",
            name="Priority Test Policy",
            rules=[rule_low, rule_high, rule_mid],
        )

        sorted_rules = policy.get_sorted_rules()
        self.assertEqual([r.id for r in sorted_rules], ["rule_high", "rule_mid", "rule_low"])
        self.assertEqual([r.priority for r in sorted_rules], [100, 50, 10])

    def test_get_sorted_rules_tie_breaking(self) -> None:
        # When priorities are equal, original list order must be preserved
        rule_1 = RuleDefinition(
            id="rule_first",
            name="First in list",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value=1),
            effect=PolicyEffect.ALLOW,
            priority=50,
        )
        rule_2 = RuleDefinition(
            id="rule_second",
            name="Second in list",
            condition=ConditionRule(field="b", operator=ConditionOperator.EQ, value=2),
            effect=PolicyEffect.ALLOW,
            priority=50,
        )

        policy = PolicyCreate(
            id="pol_tie",
            name="Tie Breaking Policy",
            rules=[rule_1, rule_2],
        )

        sorted_rules = policy.get_sorted_rules()
        self.assertEqual([r.id for r in sorted_rules], ["rule_first", "rule_second"])

    def test_sort_policies_priority_ordering(self) -> None:
        rule = self._create_sample_rule("rule_1")
        p_low = PolicyCreate(id="pol_low", name="Low", rules=[rule], priority=10)
        p_high = PolicyCreate(id="pol_high", name="High", rules=[rule], priority=100)
        p_mid = PolicyCreate(id="pol_mid", name="Mid", rules=[rule], priority=50)

        sorted_pols = sort_policies([p_low, p_high, p_mid])
        self.assertEqual([p.id for p in sorted_pols], ["pol_high", "pol_mid", "pol_low"])
        self.assertEqual([p.priority for p in sorted_pols], [100, 50, 10])

    def test_sort_policies_tie_breaking_by_id(self) -> None:
        rule = self._create_sample_rule("rule_1")
        # Equal priority 50, defined in reverse alphabetical order
        p_z = PolicyCreate(id="pol_zebra", name="Zebra", rules=[rule], priority=50)
        p_a = PolicyCreate(id="pol_alpha", name="Alpha", rules=[rule], priority=50)
        p_m = PolicyCreate(id="pol_mango", name="Mango", rules=[rule], priority=50)

        sorted_pols = sort_policies([p_z, p_a, p_m])
        # Deterministic tie-breaker must order by policy ID ascending
        self.assertEqual([p.id for p in sorted_pols], ["pol_alpha", "pol_mango", "pol_zebra"])


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyReadAndSerialization(unittest.TestCase):
    """Verifies PolicyRead metadata and JSON round-trip serialization."""

    def test_policy_read_metadata(self) -> None:
        rule = RuleDefinition(
            id="rule_1",
            name="Rule 1",
            condition=ConditionRule(field="a", operator=ConditionOperator.EQ, value=1),
            effect=PolicyEffect.ALLOW,
            priority=10,
        )
        now = datetime.now()
        read_policy = PolicyRead(
            id="pol_read",
            name="Read Policy",
            rules=[rule],
            version=2,
            created_at=now,
            updated_at=now,
        )
        self.assertEqual(read_policy.version, 2)
        self.assertEqual(read_policy.created_at, now)

    def test_policy_round_trip_json(self) -> None:
        compound_condition = ConditionGroup(
            all_of=[
                ConditionRule(field="user.role", operator=ConditionOperator.EQ, value="operator"),
                ConditionRule(field="action.cost", operator=ConditionOperator.LTE, value=250),
            ]
        )
        rule = RuleDefinition(
            id="rule_complex",
            name="Complex Rule",
            description="Multi-condition rule",
            condition=compound_condition,
            effect=PolicyEffect.ALLOW,
            priority=75,
            target_actions=["run_query"],
        )
        original_policy = PolicyCreate(
            id="pol_roundtrip",
            name="Round Trip Policy",
            description="Testing lossless serialization",
            rules=[rule],
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
        )

        # Dump to dict and re-validate
        dumped = original_policy.model_dump()
        rehydrated = PolicyCreate.model_validate(dumped)

        self.assertEqual(original_policy.id, rehydrated.id)
        self.assertEqual(original_policy.name, rehydrated.name)
        self.assertEqual(original_policy.resolution_algorithm, rehydrated.resolution_algorithm)
        self.assertEqual(original_policy.default_effect, rehydrated.default_effect)
        self.assertEqual(len(rehydrated.rules), 1)
        self.assertEqual(rehydrated.rules[0].id, "rule_complex")
        self.assertEqual(rehydrated.rules[0].priority, 75)


if __name__ == "__main__":
    unittest.main()
