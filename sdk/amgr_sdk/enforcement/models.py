"""Typed data models and enumeration contracts for AMGR runtime enforcement.

Matches the authoritative Member 3 Policy Engine and Runtime Enforcement specifications
and backend schemas in `backend/app/schemas/authorization.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PolicyEffect(str, Enum):
    """Supported outcomes for runtime authorization policy evaluation.

    - ALLOW: The evaluated action is permitted to proceed.
    - BLOCK: The evaluated action is blocked and halted at the runtime boundary.
    - REQUIRE_APPROVAL: The evaluated action requires explicit human/operator intervention.
    """

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


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
    Enforces a strict schema (extra='forbid') to prevent undocumented parameters.
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
            raise ValueError("trace_id must be a non-empty string.")
        return v.strip()

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("action must be a non-empty string.")
        return v.strip()

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("resource must be a non-empty string.")
        return v.strip()

    @model_validator(mode="before")
    @classmethod
    def _coerce_and_adapt_legacy_fields(cls, data: Any) -> Any:
        """Adapts legacy SDK constructor parameters and ComponentRef instances to the backend contract."""
        if not isinstance(data, dict):
            return data

        adapted = dict(data)

        # 1. Adapt trace_id from flow_id if trace_id is not provided
        if "trace_id" not in adapted:
            if "flow_id" in adapted and adapted["flow_id"] is not None:
                adapted["trace_id"] = str(adapted["flow_id"])
            elif "request_id" in adapted and adapted["request_id"] is not None:
                adapted["trace_id"] = str(adapted["request_id"])

        # 2. Adapt resource from destination if resource is not provided
        if "resource" not in adapted and "destination" in adapted:
            dest = adapted["destination"]
            if hasattr(dest, "component_id"):
                adapted["resource"] = str(dest.component_id)
            elif isinstance(dest, dict) and "component_id" in dest:
                adapted["resource"] = str(dest["component_id"])
            else:
                adapted["resource"] = str(dest)
        elif "resource" in adapted:
            res = adapted["resource"]
            if hasattr(res, "component_id"):
                adapted["resource"] = str(res.component_id)
            elif isinstance(res, dict) and "component_id" in res:
                adapted["resource"] = str(res["component_id"])
            elif not isinstance(res, str):
                adapted["resource"] = str(res)

        # 3. Adapt subject if it is a ComponentRef or custom model
        if "subject" in adapted:
            subj = adapted["subject"]
            if hasattr(subj, "model_dump"):
                adapted["subject"] = subj.model_dump(mode="json")
            elif hasattr(subj, "__dict__"):
                adapted["subject"] = vars(subj)

        # 4. Consolidate context, data_labels, and metadata
        ctx = dict(adapted.get("context") or {})
        if "data_labels" in adapted and adapted["data_labels"]:
            labels = adapted["data_labels"]
            ctx.setdefault("data_labels", list(labels) if isinstance(labels, (list, tuple)) else [str(labels)])
        if "metadata" in adapted and isinstance(adapted["metadata"], dict):
            for k, v in adapted["metadata"].items():
                ctx.setdefault(k, v)
        adapted["context"] = ctx

        # 5. Remove legacy top-level keys so extra="forbid" does not fail
        legacy_keys = ("flow_id", "correlation_id", "destination", "data_labels", "metadata", "request_id")
        for k in legacy_keys:
            adapted.pop(k, None)

        return adapted

    def to_eval_context(self) -> dict[str, Any]:
        """Flattens request attributes into an evaluation mapping matching the backend policy engine."""
        eval_ctx: dict[str, Any] = {}
        if isinstance(self.context, dict):
            eval_ctx.update(self.context)

        eval_ctx["action"] = self.action
        eval_ctx["resource"] = self.resource
        eval_ctx["subject"] = self.subject
        eval_ctx["trace_id"] = self.trace_id
        eval_ctx["context"] = self.context
        return eval_ctx


class AuthorizationDecision(BaseModel):
    """Deterministic policy decision produced at the runtime enforcement boundary.

    Enforces exact contract alignment with Member 3 backend schema and provides
    convenience properties (`is_allowed`, `is_blocked`, `requires_approval`, `approval_id`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: PolicyEffect = Field(
        ...,
        description="Final deterministic outcome (ALLOW, BLOCK, REQUIRE_APPROVAL)",
    )
    policy_id: str | None = Field(
        default=None,
        description="Identifier of the policy that rendered the decision",
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
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the evaluation completed",
    )
    approval_id: str | None = Field(
        default=None,
        description="Governance approval identifier when decision is REQUIRE_APPROVAL",
    )

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, v: Any) -> PolicyEffect:
        if isinstance(v, PolicyEffect):
            return v
        if isinstance(v, str):
            clean = v.strip().upper()
            if clean in PolicyEffect.__members__:
                return PolicyEffect(clean)
            if clean == "DENY":
                return PolicyEffect.BLOCK
            if clean == "REVIEW":
                return PolicyEffect.REQUIRE_APPROVAL
        raise ValueError(f"Invalid policy decision effect: {v!r}. Must be ALLOW, BLOCK, or REQUIRE_APPROVAL.")

    @model_validator(mode="before")
    @classmethod
    def _adapt_legacy_decision_fields(cls, data: Any) -> Any:
        """Adapts legacy SDK decision fields (outcome, reason) to backend contract."""
        if not isinstance(data, dict):
            return data

        adapted = dict(data)
        if "decision" not in adapted and "outcome" in adapted:
            adapted["decision"] = adapted.pop("outcome")
        if "reason_message" not in adapted and "reason" in adapted:
            adapted["reason_message"] = adapted.pop("reason")
        if "reason_code" not in adapted:
            adapted["reason_code"] = ReasonCode.RULE_MATCHED.value
        if "policy_ids" in adapted and "matched_policy_ids" not in adapted:
            raw_pids = adapted.pop("policy_ids")
            if isinstance(raw_pids, (list, tuple)):
                adapted["matched_policy_ids"] = [str(p) for p in raw_pids]
        adapted.pop("decision_id", None)
        adapted.pop("request_id", None)
        return adapted

    @property
    def is_allowed(self) -> bool:
        """True if the decision permits execution."""
        return self.decision == PolicyEffect.ALLOW

    @property
    def is_blocked(self) -> bool:
        """True if the decision explicitly blocks execution."""
        return self.decision == PolicyEffect.BLOCK

    @property
    def requires_approval(self) -> bool:
        """True if the decision requires human review and operator approval."""
        return self.decision == PolicyEffect.REQUIRE_APPROVAL

    # Backward-compatibility accessors for existing decorators and callers
    @property
    def outcome(self) -> str:
        """Legacy outcome string ('allow', 'deny', 'review')."""
        if self.is_allowed:
            return "allow"
        if self.is_blocked:
            return "deny"
        return "review"

    @property
    def reason(self) -> str:
        """Legacy alias for reason_message."""
        return self.reason_message

    @property
    def policy_ids(self) -> list[str]:
        """Legacy alias returning matched policy IDs."""
        if self.matched_policy_ids:
            return self.matched_policy_ids
        return [self.policy_id] if self.policy_id else []

    @property
    def decision_id(self) -> str:
        """Legacy decision identifier alias."""
        return self.matched_rule_id or ""

    @property
    def request_id(self) -> str:
        """Legacy request identifier alias."""
        return ""
