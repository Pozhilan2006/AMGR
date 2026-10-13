"""Authorization request and decision contracts for AMGR runtime enforcement.

Defines AuthorizationRequest, AuthorizationDecision, and ReasonCode schemas following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator

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


from backend.app.schemas.policy_enums import PolicyEffect

_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


class ReasonCode(str, Enum):
    """Standardized machine-readable explanation codes for policy decisions."""

    RULE_MATCHED = "RULE_MATCHED"
    DEFAULT_FALLBACK = "DEFAULT_FALLBACK"
    POLICY_DISABLED = "POLICY_DISABLED"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    INVALID_REQUEST = "INVALID_REQUEST"
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"


class AuthorizationRequest(BaseModel):
    """Inbound request payload for runtime policy evaluation.

    Encapsulates caller identity (subject), operation (action), target (resource),
    and execution attributes (context) alongside flow correlation tracing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str = Field(
        ...,
        description="Correlation identifier linking evaluation to SDK flow context",
    )
    subject: dict[str, Any] | str = Field(
        ...,
        description="Caller identity or component reference initiating the action",
    )
    action: str = Field(
        ...,
        description="Operation being attempted (e.g. 'execute_tool', 'query_db')",
    )
    resource: str = Field(
        ...,
        description="Target resource identifier, URI, or tool name",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured execution context and environment attributes",
    )

    @field_validator("trace_id")
    @classmethod
    def validate_trace_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Trace ID must be a non-empty string.")
        return v.strip()

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Action must be a non-empty string.")
        return v.strip()

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Resource must be a non-empty string.")
        return v.strip()

    def to_eval_context(self) -> dict[str, Any]:
        """Flattens request attributes into an evaluation mapping for condition resolution."""
        eval_ctx: dict[str, Any] = {}

        # Merge execution context first
        if isinstance(self.context, dict):
            eval_ctx.update(self.context)

        # Standard top-level accessors
        eval_ctx["action"] = self.action
        eval_ctx["resource"] = self.resource
        eval_ctx["subject"] = self.subject
        eval_ctx["trace_id"] = self.trace_id
        eval_ctx["context"] = self.context

        return eval_ctx


class AuthorizationDecision(BaseModel):
    """Deterministic policy decision produced at the runtime enforcement boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: PolicyEffect = Field(
        ...,
        description="Final deterministic outcome (ALLOW, BLOCK, REQUIRE_APPROVAL)",
    )
    policy_id: str | None = Field(
        default=None,
        description="Identifier of the policy that rendered the decision",
    )
    policy_version: int | None = Field(
        default=None,
        description="Version number of the policy that rendered the decision",
    )
    matched_rule_id: str | None = Field(
        default=None,
        description="Identifier of the primary rule that dictated the outcome",
    )
    matched_rule_ids: list[str] = Field(
        default_factory=list,
        description="Identifiers of all rules that matched during evaluation",
    )
    matched_policy_ids: list[str] = Field(
        default_factory=list,
        description="Identifiers of all policies that matched during evaluation",
    )
    reason_code: ReasonCode | str = Field(
        ...,
        description="Standardized machine-readable decision code",
    )
    reason_message: str = Field(
        ...,
        description="Human-readable justification for the decision",
    )
    missing_attributes: list[str] = Field(
        default_factory=list,
        description="Context attribute paths found missing during evaluation",
    )
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the evaluation completed",
    )
