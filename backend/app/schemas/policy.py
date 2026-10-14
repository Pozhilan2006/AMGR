"""Policy rule and policy definition schemas for AMGR.

Defines RuleDefinition, PolicyCreate, and PolicyRead schemas following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

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


from backend.app.schemas.authorization import AuthorizationRequest, ReasonCode
from backend.app.schemas.condition import ConditionGroup, ConditionRule
from backend.app.schemas.policy_enums import (
    PolicyChangeType,
    PolicyEffect,
    RuleResolutionAlgorithm,
)

# Regex enforcing safe identifier names for rule IDs and policy IDs
_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


class RuleDefinition(BaseModel):
    """Declarative definition of a single policy rule.

    Connects a condition AST (ConditionRule or ConditionGroup) with an enforcement effect,
    execution priority, and optional target scope filters.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        ...,
        description="Unique identifier for the rule within a policy (e.g. 'rule_block_unauthorized_tools')",
    )
    name: str = Field(
        ...,
        description="Human-readable title describing rule purpose",
    )
    description: str | None = Field(
        default=None,
        description="Optional detailed description of the rule rationale",
    )
    condition: ConditionRule | ConditionGroup = Field(
        ...,
        description="Deterministic condition AST evaluated against request context",
    )
    effect: PolicyEffect = Field(
        ...,
        description="Enforcement outcome produced when the condition matches (ALLOW, BLOCK, REQUIRE_APPROVAL)",
    )
    priority: int = Field(
        default=0,
        ge=0,
        description="Rule evaluation priority (higher integer indicates higher precedence)",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this rule is active during policy evaluation",
    )
    target_actions: list[str] | None = Field(
        default=None,
        description="Optional list of action names this rule applies to; if None, applies to all actions",
    )
    target_resources: list[str] | None = Field(
        default=None,
        description="Optional list of resource identifiers this rule applies to; if None, applies to all resources",
    )

    @field_validator("id")
    @classmethod
    def validate_rule_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Rule ID must be a non-empty string.")
        trimmed = v.strip()
        if not _ID_PATTERN.match(trimmed):
            raise ValueError(
                f"Rule ID '{trimmed}' is invalid. Must be an alphanumeric identifier (e.g. 'rule_1', 'block_sql')."
            )
        if trimmed.startswith("__"):
            raise ValueError("Rule ID cannot start with dunder '__'.")
        return trimmed

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Rule name must be a non-empty string.")
        return v.strip()


class PolicyCreate(BaseModel):
    """Schema for defining and creating a multi-rule policy.

    Enforces unique rule IDs, deterministic priority sorting, and explicit fail-closed defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        ...,
        description="Unique identifier for the policy (e.g. 'pol_agent_boundary_v1')",
    )
    name: str = Field(
        ...,
        description="Human-readable name of the policy",
    )
    description: str | None = Field(
        default=None,
        description="Optional detailed description of the policy scope",
    )
    priority: int = Field(
        default=0,
        ge=0,
        description="Numerical priority of the policy; higher values indicate higher precedence",
    )
    rules: list[RuleDefinition] = Field(
        ...,
        min_length=1,
        description="Ordered collection of rule definitions",
    )
    resolution_algorithm: RuleResolutionAlgorithm = Field(
        default=RuleResolutionAlgorithm.FIRST_APPLICABLE,
        description="Strategy for resolving decisions when multiple rules match",
    )
    default_effect: PolicyEffect = Field(
        default=PolicyEffect.BLOCK,
        description="Fallback effect when zero rules match (defaults to BLOCK for fail-closed security)",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the policy is active",
    )

    @field_validator("id")
    @classmethod
    def validate_policy_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Policy ID must be a non-empty string.")
        trimmed = v.strip()
        if not _ID_PATTERN.match(trimmed):
            raise ValueError(
                f"Policy ID '{trimmed}' is invalid. Must be an alphanumeric identifier (e.g. 'pol_tools')."
            )
        if trimmed.startswith("__"):
            raise ValueError("Policy ID cannot start with dunder '__'.")
        return trimmed

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Policy name must be a non-empty string.")
        return v.strip()

    @model_validator(mode="after")
    def validate_unique_rule_ids(self) -> PolicyCreate:
        seen_ids: set[str] = set()
        duplicates: list[str] = []

        for rule in self.rules:
            if rule.id in seen_ids:
                duplicates.append(rule.id)
            seen_ids.add(rule.id)

        if duplicates:
            raise ValueError(
                f"Policy contains duplicate rule IDs: {sorted(set(duplicates))}. Rule IDs must be unique within a policy."
            )

        return self

    def get_sorted_rules(self) -> list[RuleDefinition]:
        """Returns rules sorted by priority descending, preserving definition order on ties."""
        return sorted(self.rules, key=lambda r: -r.priority)


class PolicyRead(PolicyCreate):
    """Schema for reading a policy with version and audit metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(
        default=1,
        ge=1,
        description="Immutable policy version number",
    )
    created_at: datetime | str | None = Field(
        default=None,
        description="Creation timestamp",
    )
    updated_at: datetime | str | None = Field(
        default=None,
        description="Last modification timestamp",
    )


class PolicyUpdate(BaseModel):
    """Schema for updating an existing policy.

    All attributes are optional; unspecified attributes preserve earlier values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(
        default=None,
        description="Updated human-readable name of the policy",
    )
    description: str | None = Field(
        default=None,
        description="Updated detailed description of the policy scope",
    )
    priority: int | None = Field(
        default=None,
        ge=0,
        description="Updated numerical priority",
    )
    rules: list[RuleDefinition] | None = Field(
        default=None,
        min_length=1,
        description="Updated collection of rule definitions",
    )
    resolution_algorithm: RuleResolutionAlgorithm | None = Field(
        default=None,
        description="Updated strategy for resolving rule conflicts",
    )
    default_effect: PolicyEffect | None = Field(
        default=None,
        description="Updated fallback effect",
    )
    enabled: bool | None = Field(
        default=None,
        description="Updated active state",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ValueError("Policy name must be a non-empty string.")
            return v.strip()
        return None

    @model_validator(mode="after")
    def validate_unique_rule_ids(self) -> PolicyUpdate:
        if self.rules is not None:
            seen_ids: set[str] = set()
            duplicates: list[str] = []
            for rule in self.rules:
                if rule.id in seen_ids:
                    duplicates.append(rule.id)
                seen_ids.add(rule.id)
            if duplicates:
                raise ValueError(
                    f"Policy contains duplicate rule IDs: {sorted(set(duplicates))}. Rule IDs must be unique within a policy."
                )
        return self


class PolicyHistoryEntry(BaseModel):
    """Audit entry recording a historical change to a policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(
        ...,
        description="Identifier of the policy changed",
    )
    version: int = Field(
        ...,
        ge=1,
        description="Policy version revision number produced by this change",
    )
    change_type: PolicyChangeType | str = Field(
        ...,
        description="Classification of the change (CREATED, UPDATED, ACTIVATED, DEACTIVATED)",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the change was recorded",
    )
    actor: str | None = Field(
        default=None,
        description="Identity or system component responsible for the change, if available",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description or rationale for the change",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured change metadata",
    )


def sort_policies(
    policies: Sequence[PolicyCreate | PolicyRead],
) -> list[PolicyCreate | PolicyRead]:
    """Deterministically sorts policies by priority descending, tie-breaking by policy ID ascending."""
    return sorted(policies, key=lambda p: (-p.priority, p.id))


class FieldChange(BaseModel):
    """Represents an attribute difference between two policy versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old: Any = Field(default=None, description="Previous value")
    new: Any = Field(default=None, description="New value")


class RuleDiff(BaseModel):
    """Represents differences in an individual rule between revisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., description="Identifier of the rule")
    change_type: str = Field(..., description="Change classification: 'ADDED', 'REMOVED', or 'MODIFIED'")
    changes: dict[str, FieldChange] = Field(
        default_factory=dict,
        description="Field-by-field differences if rule was modified",
    )
    rule: RuleDefinition | None = Field(
        default=None,
        description="Full rule definition if added or removed",
    )


class PolicyDiff(BaseModel):
    """Semantic comparison between two policy revisions or against a proposed update."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(..., description="Identifier of the policy compared")
    from_version: int = Field(..., description="Base version number")
    to_version: int | None = Field(
        default=None,
        description="Target version number, or None if comparing against a proposed update",
    )
    has_changes: bool = Field(..., description="Whether any differences exist between versions")
    metadata_changes: dict[str, FieldChange] = Field(
        default_factory=dict,
        description="Differences in policy-level attributes (name, description, priority, enabled, algorithms)",
    )
    rules_added: list[RuleDefinition] = Field(
        default_factory=list,
        description="Rules present in target but not in base",
    )
    rules_removed: list[RuleDefinition] = Field(
        default_factory=list,
        description="Rules present in base but not in target",
    )
    rules_modified: list[RuleDiff] = Field(
        default_factory=list,
        description="Rules modified between versions with detailed attribute changes",
    )


class RuleSimulationTrace(BaseModel):
    """Detailed evaluation trace of an individual rule during policy simulation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., description="Unique rule identifier")
    rule_name: str = Field(..., description="Human-readable rule name")
    priority: int = Field(..., description="Rule priority")
    effect: PolicyEffect = Field(..., description="Rule effect (ALLOW, BLOCK, REQUIRE_APPROVAL)")
    matched: bool = Field(..., description="Whether rule condition matched the hypothetical request")
    scope_matched: bool = Field(..., description="Whether target_actions and target_resources matched")
    missing_attributes: list[str] = Field(
        default_factory=list,
        description="Attributes required by condition that were missing from request context",
    )
    reason: str = Field(..., description="Explanation of evaluation outcome for this rule")


class ConflictDetectionResult(BaseModel):
    """Analysis of rule conflicts, shadows, or redundancies within a policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., description="Rule identifier subject to conflict, shadow, or redundancy")
    shadowed_by_rule_id: str | None = Field(
        default=None,
        description="Higher priority or preceding rule that shadows or overrides this rule",
    )
    conflict_type: str = Field(
        ...,
        description="Conflict classification: 'SHADOWED', 'REDUNDANT', 'OPPOSING_EFFECT', or 'DISABLED_RULE'",
    )
    description: str = Field(..., description="Detailed explanation of the detected conflict or shadowing")


class PolicySimulationRequest(BaseModel):
    """Payload for simulating policy evaluation against a hypothetical authorization request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AuthorizationRequest = Field(
        ...,
        description="Hypothetical authorization request to evaluate",
    )
    candidate_policy: PolicyCreate | None = Field(
        default=None,
        description="Optional draft/candidate policy definition to simulate without persistence",
    )
    policy_id: str | None = Field(
        default=None,
        description="Optional ID of existing persisted policy to simulate against",
    )
    version: int | None = Field(
        default=None,
        description="Specific historical revision version to simulate against (for existing policy)",
    )


class PolicySimulationResponse(BaseModel):
    """Outcome of policy simulation containing simulated decision, rule traces, and conflict analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(..., description="Identifier of the policy evaluated")
    policy_version: int | None = Field(
        default=None,
        description="Version number of the policy evaluated, if known",
    )
    decision: PolicyEffect = Field(
        ...,
        description="Simulated decision effect (ALLOW, BLOCK, REQUIRE_APPROVAL)",
    )
    matched_rule_id: str | None = Field(
        default=None,
        description="Primary rule ID that determined the decision outcome",
    )
    matched_rule_ids: list[str] = Field(
        default_factory=list,
        description="All rule IDs that satisfied conditions for this request",
    )
    reason_code: ReasonCode = Field(..., description="Standardized reason code")
    reason_message: str = Field(..., description="Human-readable decision explanation")
    missing_attributes: list[str] = Field(
        default_factory=list,
        description="Attributes required by conditions that were missing from request context",
    )
    rule_traces: list[RuleSimulationTrace] = Field(
        default_factory=list,
        description="Ordered trace of all rules evaluated in execution order",
    )
    conflicts: list[ConflictDetectionResult] = Field(
        default_factory=list,
        description="Detected rule conflicts, shadows, or redundancies within the policy",
    )
    simulated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of simulation execution",
    )


class PolicyRollbackRequest(BaseModel):
    """Payload for rolling back an active or retired policy to a historical revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_version: int = Field(
        ...,
        ge=1,
        description="Target historical revision version number to restore",
    )
    description: str | None = Field(
        default=None,
        description="Optional human-readable audit description or justification for the rollback",
    )
