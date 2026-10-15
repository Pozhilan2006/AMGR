"""Human approval workflow schemas and state contracts for AMGR.

Defines schemas for pending human approvals, decision submissions,
and approval lifecycle states following the Member 3 specification.
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


from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from backend.app.schemas.policy_enums import PolicyEffect


class ApprovalStatus(str, Enum):
    """Lifecycle status of a human approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalDecisionType(str, Enum):
    """Explicit human action submitted for an approval request."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApprovalDecisionSubmission(BaseModel):
    """Payload submitted by a human reviewer to resolve a pending approval request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str = Field(
        ...,
        description="Authenticated human reviewer identity submitting the decision",
    )
    decision: ApprovalDecisionType = Field(
        ...,
        description="Explicit human decision (APPROVE or REJECT)",
    )
    reason: str | None = Field(
        default=None,
        description="Justification or audit note explaining the decision",
    )
    is_ai_or_llm: bool = Field(
        default=False,
        description="Safety flag indicating if the submitter is an AI or LLM operator (must be False)",
    )

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Reviewer actor identity must be a non-empty string.")
        trimmed = v.strip()
        lower = trimmed.lower()
        if lower.startswith("ai:") or lower.startswith("llm:"):
            raise ValueError(
                f"Actor identity '{trimmed}' represents an automated AI/LLM entity. "
                "Only authorized human actors may resolve approval requests."
            )
        return trimmed

    @field_validator("is_ai_or_llm")
    @classmethod
    def validate_human_only(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "Automated AI/LLM entities are forbidden from approving governance requests. "
                "Explicit human review is required."
            )
        return False


class ApprovalRequest(BaseModel):
    """Immutable state record for an approval request and its resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        ...,
        description="Unique identifier for the approval request (e.g. 'appr_01')",
    )
    trace_id: str = Field(
        ...,
        description="Correlation trace identifier from the initial authorization request",
    )
    policy_id: str = Field(
        ...,
        description="Identifier of the policy that required approval",
    )
    policy_version: int = Field(
        ...,
        ge=1,
        description="Policy version at the time the approval was requested",
    )
    matched_rule_id: str | None = Field(
        default=None,
        description="Rule within the policy that triggered REQUIRE_APPROVAL",
    )
    original_decision: AuthorizationDecision = Field(
        ...,
        description="Snapshot of the original deterministic policy evaluation",
    )
    request_payload: AuthorizationRequest = Field(
        ...,
        description="Snapshot of the original inbound request payload",
    )
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Current lifecycle state of the approval request",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the approval request was created",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the decision was finalized",
    )
    decided_by: str | None = Field(
        default=None,
        description="Authenticated human reviewer who finalized the decision",
    )
    decision_reason: str | None = Field(
        default=None,
        description="Human justification recorded for the decision",
    )
    final_effect: PolicyEffect = Field(
        default=PolicyEffect.REQUIRE_APPROVAL,
        description="Effective enforcement permission state (only ALLOW permits execution)",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the approval request expires",
    )

    def is_pending(self, now: datetime | None = None) -> bool:
        """Returns True if the approval is pending human action and has not expired."""
        if self.status != ApprovalStatus.PENDING:
            return False
        if self.expires_at is not None:
            current = now if now is not None else datetime.now(timezone.utc)
            if current >= self.expires_at:
                return False
        return True

    def is_approved(self, now: datetime | None = None) -> bool:
        """Returns True only if the request has been explicitly approved and has not expired."""
        if self.status != ApprovalStatus.APPROVED or self.final_effect != PolicyEffect.ALLOW:
            return False
        if self.expires_at is not None:
            current = now if now is not None else datetime.now(timezone.utc)
            if current >= self.expires_at:
                return False
        return True

    def is_expired(self, now: datetime | None = None) -> bool:
        """Returns True if the approval is explicitly EXPIRED or has passed expires_at."""
        if self.status == ApprovalStatus.EXPIRED:
            return True
        if self.expires_at is not None:
            current = now if now is not None else datetime.now(timezone.utc)
            return current >= self.expires_at
        return False

    def is_terminal(self) -> bool:
        """Returns True if the approval request has reached a terminal state."""
        return self.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        )


class ApprovalCancellationSubmission(BaseModel):
    """Payload submitted by a human reviewer or operator to cancel/invalidate an approval request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str = Field(
        ...,
        description="Authenticated human reviewer identity submitting the cancellation",
    )
    reason: str = Field(
        ...,
        description="Justification or rationale for cancelling the approval request",
    )
    is_ai_or_llm: bool = Field(
        default=False,
        description="Safety flag indicating if the submitter is an AI or LLM operator (must be False)",
    )

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Actor identity must be a non-empty string.")
        trimmed = v.strip()
        lower = trimmed.lower()
        if lower.startswith("ai:") or lower.startswith("llm:"):
            raise ValueError(
                f"Actor identity '{trimmed}' represents an automated AI/LLM entity. "
                "Only authorized human actors may cancel approval requests."
            )
        return trimmed

    @field_validator("is_ai_or_llm")
    @classmethod
    def validate_human_only(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "Automated AI/LLM entities are forbidden from cancelling approval requests. "
                "Explicit human action is required."
            )
        return False
