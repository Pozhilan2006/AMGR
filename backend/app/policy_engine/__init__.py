"""AMGR Policy Engine package.

Exports deterministic condition, rule, and policy evaluation interfaces,
as well as matcher and precedence resolvers.
"""

from backend.app.policy_engine.evaluator import (
    evaluate_condition,
    evaluate_policies,
    evaluate_policy,
    evaluate_rule,
)
from backend.app.policy_engine.matcher import PolicyMatcher
from backend.app.policy_engine.precedence import PolicyPrecedence

__all__ = [
    "PolicyMatcher",
    "PolicyPrecedence",
    "evaluate_condition",
    "evaluate_policies",
    "evaluate_policy",
    "evaluate_rule",
]
