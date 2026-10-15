"""Deterministic precedence resolver and conflict resolution for policy evaluation."""

from __future__ import annotations

from typing import Any, Sequence

from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    RuleDefinition,
    sort_policies,
)
from backend.app.schemas.policy_enums import PolicyEffect, RuleResolutionAlgorithm


class PolicyPrecedence:
    """Orders policy rules and resolves conflicts deterministically following AMGR specifications."""

    def __init__(self) -> None:
        self._implemented = True

    def order_rules(self, rules: Sequence[RuleDefinition]) -> list[RuleDefinition]:
        """Sorts rules by priority descending, preserving definition order on ties."""
        return sorted(rules, key=lambda r: -r.priority)

    def order_policies(
        self,
        policies: Sequence[PolicyCreate | PolicyRead],
    ) -> list[PolicyCreate | PolicyRead]:
        """Deterministically sorts policies by priority descending, tie-breaking by policy ID ascending."""
        return sort_policies(policies)

    def group_by_priority_tier(
        self,
        policies: Sequence[PolicyCreate | PolicyRead],
    ) -> list[tuple[int, list[PolicyCreate | PolicyRead]]]:
        """Groups policies into discrete priority tiers ordered from highest to lowest priority."""
        ordered = self.order_policies(policies)
        tiers: dict[int, list[PolicyCreate | PolicyRead]] = {}
        for p in ordered:
            tiers.setdefault(p.priority, []).append(p)
        return [(prio, tiers[prio]) for prio in sorted(tiers.keys(), reverse=True)]

    def resolve_rule_conflict(
        self,
        matching_rules: Sequence[RuleDefinition],
        algorithm: RuleResolutionAlgorithm,
    ) -> RuleDefinition | None:
        """Resolves conflict among matching rules within a policy under the specified algorithm."""
        if not matching_rules:
            return None

        if algorithm == RuleResolutionAlgorithm.FIRST_APPLICABLE:
            sorted_rules = self.order_rules(matching_rules)
            return sorted_rules[0]

        if algorithm == RuleResolutionAlgorithm.BLOCK_OVERRIDES:
            for r in matching_rules:
                if r.effect == PolicyEffect.BLOCK:
                    return r
            for r in matching_rules:
                if r.effect == PolicyEffect.REQUIRE_APPROVAL:
                    return r
            return matching_rules[0]

        if algorithm == RuleResolutionAlgorithm.ALLOW_OVERRIDES:
            for r in matching_rules:
                if r.effect == PolicyEffect.ALLOW:
                    return r
            for r in matching_rules:
                if r.effect == PolicyEffect.REQUIRE_APPROVAL:
                    return r
            return matching_rules[0]

        return matching_rules[0]

    def resolve_tier_conflict(
        self,
        effects: Sequence[PolicyEffect],
        algorithm: RuleResolutionAlgorithm = RuleResolutionAlgorithm.BLOCK_OVERRIDES,
    ) -> PolicyEffect:
        """Resolves conflict among differing policy effects within the same priority tier."""
        if not effects:
            return PolicyEffect.BLOCK

        if all(e == effects[0] for e in effects):
            return effects[0]

        if algorithm == RuleResolutionAlgorithm.ALLOW_OVERRIDES:
            if PolicyEffect.ALLOW in effects:
                return PolicyEffect.ALLOW
            if PolicyEffect.REQUIRE_APPROVAL in effects:
                return PolicyEffect.REQUIRE_APPROVAL
            return PolicyEffect.BLOCK
        else:
            # Default to BLOCK_OVERRIDES for fail-closed governance
            if PolicyEffect.BLOCK in effects:
                return PolicyEffect.BLOCK
            if PolicyEffect.REQUIRE_APPROVAL in effects:
                return PolicyEffect.REQUIRE_APPROVAL
            return PolicyEffect.ALLOW

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        """Dispatches conflict resolution based on arguments."""
        if args and isinstance(args[0], (list, tuple)):
            items = args[0]
            if items and isinstance(items[0], RuleDefinition):
                algo = kwargs.get("algorithm", RuleResolutionAlgorithm.FIRST_APPLICABLE)
                return self.resolve_rule_conflict(items, algo)
            if items and isinstance(items[0], PolicyEffect):
                algo = kwargs.get("algorithm", RuleResolutionAlgorithm.BLOCK_OVERRIDES)
                return self.resolve_tier_conflict(items, algo)
        raise ValueError("Invalid arguments for PolicyPrecedence.resolve")
