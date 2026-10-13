"""AMGR Policy Engine package.

Exports deterministic condition, rule, and policy evaluation interfaces.
"""

from backend.app.policy_engine.evaluator import (
    evaluate_condition,
    evaluate_policies,
    evaluate_policy,
    evaluate_rule,
)

__all__ = [
    "evaluate_condition",
    "evaluate_policies",
    "evaluate_policy",
    "evaluate_rule",
]
