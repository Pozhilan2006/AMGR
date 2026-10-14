"""Deterministic rule and condition matcher for AMGR policy evaluation."""

from __future__ import annotations

from typing import Any

from backend.app.policy_engine.evaluator import (
    _evaluate_condition_internal,
    _evaluate_rule_internal,
)
from backend.app.schemas.authorization import AuthorizationRequest
from backend.app.schemas.condition import ConditionGroup, ConditionRule
from backend.app.schemas.policy import RuleDefinition


class PolicyMatcher:
    """Determines whether policy conditions and rules match a runtime event or request."""

    def __init__(self) -> None:
        self._implemented = True

    def match_condition(
        self,
        condition: ConditionRule | ConditionGroup,
        eval_context: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Evaluates a condition AST against request attributes, returning (matched, missing_attrs)."""
        res = _evaluate_condition_internal(condition, eval_context)
        return res.matched, res.missing_attributes

    def match_rule(
        self,
        rule: RuleDefinition,
        request: AuthorizationRequest | dict[str, Any],
    ) -> tuple[bool, bool, list[str]]:
        """Evaluates scope filters and conditions of a rule against request.

        Returns (matched, scope_matched, missing_attributes).
        """
        if isinstance(request, dict):
            request = AuthorizationRequest.model_validate(request)
        trace = _evaluate_rule_internal(rule, request)
        return trace.matched, trace.applicable, trace.missing_attributes

    def match(self, target: Any, context_or_request: Any) -> bool:
        """General matching interface: checks if a rule or condition matches."""
        if isinstance(target, RuleDefinition):
            matched, _, _ = self.match_rule(target, context_or_request)
            return matched
        elif isinstance(target, (ConditionRule, ConditionGroup)):
            if isinstance(context_or_request, AuthorizationRequest):
                eval_context = context_or_request.to_eval_context()
            elif isinstance(context_or_request, dict):
                eval_context = context_or_request
            else:
                eval_context = {}
            matched, _ = self.match_condition(target, eval_context)
            return matched
        raise TypeError(f"Unsupported target type for PolicyMatcher: {type(target)}")
