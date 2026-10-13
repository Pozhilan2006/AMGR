"""Unit tests for AMGR policy enums and condition schemas.

Validates policy effects, condition operators, operand type safety,
malformed conditions, arbitrary expression rejection, attribute resolution,
and boundary cases.
"""

import unittest

from backend.app.schemas.condition import (
    MAX_CONDITION_DEPTH,
    PYDANTIC_AVAILABLE,
    ConditionGroup,
    ConditionRule,
    resolve_attribute,
)
from backend.app.schemas.policy_enums import (
    AttributeResolutionStatus,
    ConditionOperator,
    PolicyEffect,
)

try:
    from pydantic import ValidationError
except ModuleNotFoundError:
    ValidationError = Exception  # type: ignore[assignment, misc]



class TestPolicyEnums(unittest.TestCase):
    """Verifies enum membership matches exact Member 3 specifications."""

    def test_policy_effect_members(self) -> None:
        expected = {"ALLOW", "BLOCK", "REQUIRE_APPROVAL"}
        actual = {effect.value for effect in PolicyEffect}
        self.assertEqual(actual, expected)

    def test_condition_operator_members(self) -> None:
        expected = {
            "EQ",
            "NOT_EQ",
            "IN",
            "NOT_IN",
            "CONTAINS",
            "STARTS_WITH",
            "ENDS_WITH",
            "GT",
            "GTE",
            "LT",
            "LTE",
            "EXISTS",
            "NOT_EXISTS",
        }
        actual = {op.value for op in ConditionOperator}
        self.assertEqual(actual, expected)


class TestAttributeResolution(unittest.TestCase):
    """Verifies deterministic context extraction and safe handling of missing/unknown attributes."""

    def test_resolve_flat_and_nested_attributes(self) -> None:
        context = {
            "action": "execute_query",
            "subject": {"role": "admin", "department": "security"},
            "resource": {"metadata": {"sensitivity": "high"}},
        }

        status, val = resolve_attribute(context, "action")
        self.assertEqual(status, AttributeResolutionStatus.FOUND)
        self.assertEqual(val, "execute_query")

        status, val = resolve_attribute(context, "subject.role")
        self.assertEqual(status, AttributeResolutionStatus.FOUND)
        self.assertEqual(val, "admin")

        status, val = resolve_attribute(context, "resource.metadata.sensitivity")
        self.assertEqual(status, AttributeResolutionStatus.FOUND)
        self.assertEqual(val, "high")

    def test_resolve_attribute_explicit_none(self) -> None:
        context = {"subject": {"tenant_id": None}}
        status, val = resolve_attribute(context, "subject.tenant_id")
        self.assertEqual(status, AttributeResolutionStatus.FOUND)
        self.assertIsNone(val)

    def test_resolve_missing_top_level_attribute(self) -> None:
        context = {"subject": {"role": "analyst"}}
        status, val = resolve_attribute(context, "destination")
        self.assertEqual(status, AttributeResolutionStatus.MISSING)
        self.assertIsNone(val)

    def test_resolve_missing_nested_attribute(self) -> None:
        context = {"subject": {"role": "analyst"}}
        status, val = resolve_attribute(context, "subject.clearance_level")
        self.assertEqual(status, AttributeResolutionStatus.MISSING)
        self.assertIsNone(val)

    def test_resolve_intermediate_non_mapping(self) -> None:
        context = {"subject": "not_a_dict"}
        status, val = resolve_attribute(context, "subject.role")
        self.assertEqual(status, AttributeResolutionStatus.MISSING)
        self.assertIsNone(val)

    def test_resolve_invalid_and_dunder_paths(self) -> None:
        context = {"subject": {"role": "admin"}}
        # Dunder injection
        status, _ = resolve_attribute(context, "__class__.__name__")
        self.assertEqual(status, AttributeResolutionStatus.INVALID_PATH)

        # Malformed paths
        status, _ = resolve_attribute(context, "")
        self.assertEqual(status, AttributeResolutionStatus.INVALID_PATH)

        status, _ = resolve_attribute(context, "subject..role")
        self.assertEqual(status, AttributeResolutionStatus.INVALID_PATH)

        status, _ = resolve_attribute(context, "subject.role;")
        self.assertEqual(status, AttributeResolutionStatus.INVALID_PATH)


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestConditionRuleValidation(unittest.TestCase):
    """Verifies validation rules on atomic ConditionRule definitions."""

    def test_valid_atomic_conditions(self) -> None:
        # Equality
        rule = ConditionRule(field="subject.role", operator=ConditionOperator.EQ, value="admin")
        self.assertEqual(rule.field, "subject.role")
        self.assertEqual(rule.operator, ConditionOperator.EQ)
        self.assertEqual(rule.value, "admin")

        # Numeric comparison
        rule_num = ConditionRule(field="request.count", operator=ConditionOperator.GT, value=100)
        self.assertEqual(rule_num.value, 100)

        rule_float = ConditionRule(field="score", operator=ConditionOperator.LTE, value=0.95)
        self.assertEqual(rule_float.value, 0.95)

        # Membership
        rule_in = ConditionRule(
            field="action.type", operator=ConditionOperator.IN, value=["read", "write"]
        )
        self.assertEqual(rule_in.value, ["read", "write"])

        # String patterns
        rule_contains = ConditionRule(
            field="resource.uri", operator=ConditionOperator.CONTAINS, value="prod-db"
        )
        self.assertEqual(rule_contains.value, "prod-db")

        # Existence (value is None)
        rule_exists = ConditionRule(field="context.session_id", operator=ConditionOperator.EXISTS)
        self.assertIsNone(rule_exists.value)

        rule_not_exists = ConditionRule(
            field="context.error", operator=ConditionOperator.NOT_EXISTS
        )
        self.assertIsNone(rule_not_exists.value)

    def test_reject_unsupported_operator(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule.model_validate(
                {"field": "user.role", "operator": "REGEX_MATCH", "value": ".*"}
            )

    def test_reject_numeric_with_string_operand(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule(field="amount", operator=ConditionOperator.GT, value="100")

    def test_reject_numeric_with_bool_operand(self) -> None:
        # Python bool is subclass of int, but must be strictly rejected
        with self.assertRaises(ValidationError):
            ConditionRule(field="amount", operator=ConditionOperator.GT, value=True)

    def test_reject_membership_with_scalar(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule(field="user.id", operator=ConditionOperator.IN, value=123)

    def test_reject_string_pattern_with_non_string(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule(field="resource.tag", operator=ConditionOperator.CONTAINS, value=123)

    def test_reject_exists_with_non_none_value(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule(
                field="session.id", operator=ConditionOperator.EXISTS, value="unexpected"
            )

    def test_reject_malformed_field_names(self) -> None:
        invalid_fields = [
            "",
            "   ",
            "eval()",
            "user;drop",
            "user['role']",
            "__import__",
            ".user.role",
            "user..role",
        ]
        for field in invalid_fields:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ConditionRule(field=field, operator=ConditionOperator.EQ, value="test")

    def test_reject_extra_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionRule.model_validate(
                {
                    "field": "user.role",
                    "operator": "EQ",
                    "value": "admin",
                    "unauthorized_field": True,
                }
            )


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestConditionGroupValidation(unittest.TestCase):
    """Verifies compound ConditionGroup trees and depth bounds."""

    def test_valid_compound_group(self) -> None:
        group = ConditionGroup(
            all_of=[
                ConditionRule(field="subject.role", operator=ConditionOperator.EQ, value="admin"),
                ConditionRule(field="action.cost", operator=ConditionOperator.LT, value=500),
            ],
            any_of=[
                ConditionRule(field="env", operator=ConditionOperator.EQ, value="staging"),
            ],
        )
        self.assertEqual(len(group.all_of), 2)
        self.assertEqual(len(group.any_of), 1)

    def test_reject_empty_group(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionGroup()

    def test_reject_empty_list_combinator(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionGroup(all_of=[])

    def test_reject_extra_fields_on_group(self) -> None:
        with self.assertRaises(ValidationError):
            ConditionGroup.model_validate(
                {
                    "all_of": [
                        {"field": "role", "operator": "EQ", "value": "admin"}
                    ],
                    "unknown_key": "injected",
                }
            )

    def test_reject_exceeding_max_depth(self) -> None:
        # Build nested groups up to MAX_CONDITION_DEPTH (5)
        # leaf (depth 1) wrapped in ConditionGroup starts at depth 2
        leaf = ConditionRule(field="status", operator=ConditionOperator.EQ, value="ok")
        current = ConditionGroup(all_of=[leaf])  # depth 2
        for _ in range(MAX_CONDITION_DEPTH - 2):
            current = ConditionGroup(all_of=[current])  # reaches depth 5

        # Exceeding MAX_CONDITION_DEPTH (depth 6) must raise ValidationError
        with self.assertRaises(ValidationError):
            ConditionGroup(all_of=[current])




@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestBoundaryCases(unittest.TestCase):
    """Verifies edge cases and boundary inputs."""

    def test_empty_string_pattern(self) -> None:
        rule = ConditionRule(field="name", operator=ConditionOperator.CONTAINS, value="")
        self.assertEqual(rule.value, "")

    def test_empty_collection_membership(self) -> None:
        rule = ConditionRule(field="role", operator=ConditionOperator.IN, value=[])
        self.assertEqual(rule.value, [])

    def test_numeric_zero_boundary(self) -> None:
        rule = ConditionRule(field="count", operator=ConditionOperator.GTE, value=0)
        self.assertEqual(rule.value, 0)
        rule_float_zero = ConditionRule(field="rate", operator=ConditionOperator.EQ, value=0.0)
        self.assertEqual(rule_float_zero.value, 0.0)


if __name__ == "__main__":
    unittest.main()
