"""Condition AST models and attribute extraction for AMGR policy evaluation.

Implements strictly validated declarative condition trees.
Arbitrary expression evaluation (eval/exec) is strictly forbidden.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

    PYDANTIC_AVAILABLE = True
except ModuleNotFoundError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment, misc]
    ConfigDict = dict  # type: ignore[assignment, misc]

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        return None

    def field_validator(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        def decorator(fn: Any) -> Any:
            return fn
        return decorator

    def model_validator(*args: Any, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        def decorator(fn: Any) -> Any:
            return fn
        return decorator


from backend.app.schemas.policy_enums import (
    AttributeResolutionStatus,
    ConditionOperator,
)

# Regex enforcing safe dotted-path identifiers: e.g. "subject.role", "action.name"
_IDENTIFIER_PATTERN = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$"
)

# Maximum nesting depth allowed for condition groups to prevent recursion DoS
MAX_CONDITION_DEPTH = 5


def resolve_attribute(
    context: Mapping[str, Any], field_path: str
) -> tuple[AttributeResolutionStatus, Any]:
    """Safely extracts a nested attribute along a dotted path from a context mapping.

    Security rules:
    - Never evaluates expressions or code.
    - Explicitly returns MISSING if any key along the path does not exist.
    - Explicitly returns INVALID_PATH if path syntax is malformed.
    - Differentiates missing attributes from attributes explicitly set to None.
    """
    if not isinstance(context, Mapping):
        return AttributeResolutionStatus.MISSING, None

    if not field_path or not _IDENTIFIER_PATTERN.match(field_path):
        return AttributeResolutionStatus.INVALID_PATH, None

    # Guard against Python dunder traversal
    if any(segment.startswith("__") for segment in field_path.split(".")):
        return AttributeResolutionStatus.INVALID_PATH, None

    parts = field_path.split(".")
    current: Any = context

    for part in parts:
        if not isinstance(current, Mapping):
            return AttributeResolutionStatus.MISSING, None
        if part not in current:
            return AttributeResolutionStatus.MISSING, None
        current = current[part]

    return AttributeResolutionStatus.FOUND, current


class ConditionRule(BaseModel):
    """Atomic condition rule evaluating a single attribute against an operator and value.

    Validation rules:
    - Strict operand typing according to the specified operator.
    - Rejection of unsupported operators and arbitrary executable expressions.
    - Rejection of unknown fields (extra='forbid').
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(
        ...,
        description="Dotted path identifier of the context attribute to inspect (e.g. 'subject.role')",
    )
    operator: ConditionOperator = Field(
        ...,
        description="Explicitly supported condition evaluation operator",
    )
    value: Any = Field(
        default=None,
        description="Target operand value to evaluate against. Required for all operators except EXISTS/NOT_EXISTS.",
    )

    @field_validator("field")
    @classmethod
    def validate_field_path(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field path must be a non-empty string.")
        trimmed = v.strip()
        if not _IDENTIFIER_PATTERN.match(trimmed):
            raise ValueError(
                f"Field path '{trimmed}' is invalid. Must be a safe dotted identifier (e.g. 'subject.role')."
            )
        if any(segment.startswith("__") for segment in trimmed.split(".")):
            raise ValueError("Access to dunder attributes is forbidden.")
        return trimmed

    @model_validator(mode="after")
    def validate_operator_and_value(self) -> ConditionRule:
        op = self.operator
        val = self.value

        # 1. Existence operators require no target value
        if op in (ConditionOperator.EXISTS, ConditionOperator.NOT_EXISTS):
            if val is not None:
                raise ValueError(
                    f"Operator '{op.value}' does not accept a comparison value; expected None, got {val!r}."
                )
            return self

        # For all other operators, value must not be omitted when required
        if op in (
            ConditionOperator.GT,
            ConditionOperator.GTE,
            ConditionOperator.LT,
            ConditionOperator.LTE,
        ):
            # Numeric operators: must be int or float, strictly NOT bool
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(
                    f"Operator '{op.value}' requires a numeric operand (int or float, not bool); got {type(val).__name__}."
                )

        elif op in (ConditionOperator.IN, ConditionOperator.NOT_IN):
            # Membership operators: must be a list, tuple, or set (not str, bytes, or scalar)
            if not isinstance(val, (list, tuple, set)):
                raise ValueError(
                    f"Operator '{op.value}' requires a collection operand (list, tuple, or set); got {type(val).__name__}."
                )

        elif op in (
            ConditionOperator.CONTAINS,
            ConditionOperator.STARTS_WITH,
            ConditionOperator.ENDS_WITH,
        ):
            # String pattern operators: must be str
            if not isinstance(val, str):
                raise ValueError(
                    f"Operator '{op.value}' requires a string operand; got {type(val).__name__}."
                )

        elif op in (ConditionOperator.EQ, ConditionOperator.NOT_EQ):
            # Equality operators: allow primitive scalar values or None, reject complex objects/functions
            if not (
                val is None
                or isinstance(val, (str, int, float, bool))
                or (isinstance(val, (list, tuple, dict)) and not callable(val))
            ):
                raise ValueError(
                    f"Operator '{op.value}' does not support complex operand type {type(val).__name__}."
                )

        return self


class ConditionGroup(BaseModel):
    """Compound condition combining multiple rules or groups with boolean semantics.

    At least one combinator (all_of, any_of, or none_of) must be defined and non-empty.
    Nesting is strictly bounded by MAX_CONDITION_DEPTH.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    all_of: list[ConditionRule | ConditionGroup] | None = Field(
        default=None,
        description="All conditions in this list must be satisfied (logical AND)",
    )
    any_of: list[ConditionRule | ConditionGroup] | None = Field(
        default=None,
        description="At least one condition in this list must be satisfied (logical OR)",
    )
    none_of: list[ConditionRule | ConditionGroup] | None = Field(
        default=None,
        description="None of the conditions in this list may be satisfied (logical NOT)",
    )

    @model_validator(mode="after")
    def validate_group_structure(self) -> ConditionGroup:
        combinators = [self.all_of, self.any_of, self.none_of]
        active = [c for c in combinators if c is not None]

        if not active:
            raise ValueError(
                "ConditionGroup must define at least one non-null combinator ('all_of', 'any_of', or 'none_of')."
            )

        # Ensure no combinator is an empty list
        for name, items in [
            ("all_of", self.all_of),
            ("any_of", self.any_of),
            ("none_of", self.none_of),
        ]:
            if items is not None and len(items) == 0:
                raise ValueError(
                    f"Combinator '{name}' cannot be an empty list when specified."
                )

        # Enforce maximum nesting depth
        depth = self._calculate_depth(self)
        if depth > MAX_CONDITION_DEPTH:
            raise ValueError(
                f"ConditionGroup nesting depth ({depth}) exceeds maximum allowed depth ({MAX_CONDITION_DEPTH})."
            )

        return self

    @classmethod
    def _calculate_depth(cls, node: ConditionRule | ConditionGroup) -> int:
        if isinstance(node, ConditionRule):
            return 1

        max_child_depth = 0
        for branch in (node.all_of, node.any_of, node.none_of):
            if branch:
                for child in branch:
                    child_depth = cls._calculate_depth(child)
                    if child_depth > max_child_depth:
                        max_child_depth = child_depth

        return 1 + max_child_depth
