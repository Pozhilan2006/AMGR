"""Adapter schemas and conversion utilities for Member 1 SDK authorization contracts.

Translates SDK-format requests into canonical backend AuthorizationRequest models
without mutating original inputs or modifying Phase 2 core evaluator logic.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.schemas.authorization import AuthorizationDecision, AuthorizationRequest
from backend.app.schemas.policy_enums import PolicyEffect

_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.:/]+$")


class SDKComponentRef(BaseModel):
    """Mirrors amgr_sdk.models.events.ComponentRef for runtime boundary validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_id: str = Field(
        ..., description="Stable identifier for the component"
    )
    component_type: str = Field(
        ..., description="Category (agent, model, api, db, tool)"
    )
    name: str | None = Field(
        default=None, description="Optional human-readable name"
    )

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("component_id must be a non-empty string.")
        trimmed = v.strip()
        if not _IDENTIFIER_PATTERN.match(trimmed):
            raise ValueError(f"component_id '{trimmed}' contains invalid characters.")
        return trimmed

    @field_validator("component_type")
    @classmethod
    def validate_component_type(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("component_type must be a non-empty string.")
        trimmed = v.strip()
        if not _IDENTIFIER_PATTERN.match(trimmed):
            raise ValueError(f"component_type '{trimmed}' contains invalid characters.")
        return trimmed


class SDKAuthorizationRequest(BaseModel):
    """Mirrors amgr_sdk.enforcement.client.AuthorizationRequest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID = Field(
        default_factory=uuid4,
        description="Unique invocation request identifier",
    )
    flow_id: UUID = Field(
        ...,
        description="Correlation flow identifier",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Optional correlated session or interaction identifier",
    )
    subject: SDKComponentRef = Field(
        ...,
        description="Origin component initiating the protected action",
    )
    action: str = Field(
        ...,
        description="Operation or tool being attempted",
    )
    resource: SDKComponentRef = Field(
        ...,
        description="Target resource component reference",
    )
    data_labels: list[str] = Field(
        default_factory=list,
        description="Security tags and sensitivity classifications",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Contextual metadata for condition AST evaluation",
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("action must be a non-empty string.")
        return v.strip()


def normalize_resource_ref(resource: SDKComponentRef) -> str:
    """Deterministically normalizes an SDK ComponentRef resource into a canonical URI string."""
    return f"{resource.component_type}://{resource.component_id}"


def convert_sdk_request_to_backend(
    sdk_req: SDKAuthorizationRequest,
) -> AuthorizationRequest:
    """Converts an SDK-formatted authorization request to the backend's canonical AuthorizationRequest.

    Preservation and normalization rules:
    - Maps `flow_id` directly to `trace_id` as string.
    - Normalizes `resource` ComponentRef to `f'{component_type}://{component_id}'`.
    - Serializes `subject` ComponentRef to dictionary mapping.
    - Preserves `action` verbatim.
    - Copies `metadata` into `context`.
    - Attaches `sdk_request_id`, `flow_id`, `data_labels`, and `correlation_id` (if present)
      into `context` without dropping any security context.
    - Preserves `approval_id` if present in `metadata` so approval workflow resumption functions seamlessly.
    - Does NOT mutate `sdk_req`.
    """
    trace_id = str(sdk_req.flow_id)
    subject_dict = sdk_req.subject.model_dump(exclude_none=True)
    resource_uri = normalize_resource_ref(sdk_req.resource)

    context: dict[str, Any] = dict(sdk_req.metadata)
    context["sdk_request_id"] = str(sdk_req.request_id)
    context["flow_id"] = str(sdk_req.flow_id)
    if sdk_req.correlation_id is not None:
        context["correlation_id"] = str(sdk_req.correlation_id)
    context["data_labels"] = list(sdk_req.data_labels)
    context["subject_details"] = subject_dict
    context["resource_details"] = sdk_req.resource.model_dump(exclude_none=True)

    return AuthorizationRequest(
        trace_id=trace_id,
        subject=subject_dict,
        action=sdk_req.action,
        resource=resource_uri,
        context=context,
    )


def map_policy_effect_to_sdk_outcome(effect: PolicyEffect) -> str:
    """Maps internal PolicyEffect enum to SDK literal outcome.

    ALLOW -> 'allow'
    BLOCK -> 'deny'
    REQUIRE_APPROVAL -> 'review'
    Any unrecognized or unexpected effect strictly fails closed and raises ValueError.
    """
    if effect == PolicyEffect.ALLOW:
        return "allow"
    elif effect == PolicyEffect.BLOCK:
        return "deny"
    elif effect == PolicyEffect.REQUIRE_APPROVAL:
        return "review"
    else:
        raise ValueError(
            f"Unknown policy effect '{effect}'; cannot safely map to SDK outcome."
        )


class PolicyIdCompatibilityError(ValueError):
    """Raised when real backend policy IDs cannot be faithfully represented in SDK UUID format.

    Preserves audit trail fidelity by refusing to synthesize random UUIDs or discard identifiers.
    """


class SDKAuthorizationDecision(BaseModel):
    """Mirrors amgr_sdk.enforcement.client.AuthorizationDecision for the enforcement endpoint response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the decision record",
    )
    request_id: UUID = Field(
        ...,
        description="Original SDK request identifier linking to the invocation",
    )
    outcome: Literal["allow", "deny", "review"] = Field(
        ...,
        description="SDK-compatible policy outcome ('allow', 'deny', 'review')",
    )
    reason: str = Field(
        ...,
        description="Human-readable justification for the decision",
    )
    policy_ids: list[str] = Field(
        default_factory=list,
        description="Identifiers of policies that evaluated to this decision",
    )
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the evaluation completed",
    )


def convert_backend_decision_to_sdk(
    decision: AuthorizationDecision,
    request_id: UUID,
    decision_id: UUID | None = None,
) -> SDKAuthorizationDecision:
    """Converts a canonical AuthorizationDecision to the SDK AuthorizationDecision contract.

    Preservation and fidelity rules:
    - Maps PolicyEffect to outcome ('allow', 'deny', 'review') via map_policy_effect_to_sdk_outcome.
    - Preserves request_id verbatim.
    - Assigns unique decision_id UUID.
    - Maps reason_message to reason.
    - Preserves evaluated_at timestamp.
    - Preserves real backend string policy IDs (e.g. 'pol_read_allow' or UUID strings)
      faithfully without synthesizing random UUIDs or discarding identifiers.
    """
    outcome = map_policy_effect_to_sdk_outcome(decision.decision)

    raw_pids: list[str] = []
    if decision.matched_policy_ids:
        raw_pids.extend(decision.matched_policy_ids)
    elif decision.policy_id:
        raw_pids.append(decision.policy_id)

    return SDKAuthorizationDecision(
        decision_id=decision_id or uuid4(),
        request_id=request_id,
        outcome=outcome,
        reason=decision.reason_message,
        policy_ids=raw_pids,
        evaluated_at=decision.evaluated_at,
    )

