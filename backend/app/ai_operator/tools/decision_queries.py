from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ai_operator.query_schemas import normalize_identifier
from app.ai_operator.schemas import OperatorScope


class DecisionQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str | None = None
    outcome: str | None = None
    reason: str | None = None
    policy_ids: list[str] = Field(default_factory=list)
    evaluated_at: datetime | None = None
    scope: str
    unavailable: bool = False
    message: str | None = None


async def explain_decision(
    decision_id: str | None,
    *,
    scope: OperatorScope,
) -> DecisionQueryResult:
    normalized = normalize_identifier(decision_id)
    if not normalized:
        raise ValueError("A decision identifier is required for policy decision explanation.")

    return DecisionQueryResult(
        decision_id=normalized,
        outcome=None,
        reason=None,
        policy_ids=[],
        evaluated_at=None,
        scope=f"{scope.application_id}:{scope.environment}",
        unavailable=True,
        message=(
            "No matching decision record is available from the implemented AMGR interfaces; "
            "authorization decision history is not yet exposed in this repository."
        ),
    )


async def find_decisions_for_flow(
    flow_id: str | None,
    *,
    scope: OperatorScope,
    limit: int = 10,
) -> list[DecisionQueryResult]:
    normalized = normalize_identifier(flow_id)
    if not normalized:
        raise ValueError("A flow identifier is required for decision history lookup.")
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return [
        DecisionQueryResult(
            decision_id=None,
            outcome=None,
            reason=None,
            policy_ids=[],
            evaluated_at=None,
            scope=f"{scope.application_id}:{scope.environment}",
            unavailable=True,
            message=(
                "No matching historical decision records are available in the current "
                "repository implementation."
            ),
        )
    ]


async def list_recent_decisions(
    *,
    scope: OperatorScope,
    limit: int = 10,
) -> list[DecisionQueryResult]:
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return [
        DecisionQueryResult(
            decision_id=None,
            outcome=None,
            reason=None,
            policy_ids=[],
            evaluated_at=None,
            scope=f"{scope.application_id}:{scope.environment}",
            unavailable=True,
            message=(
                "Decision history is not implemented in this repository, so no recent "
                "authoritative decisions can be listed."
            ),
        )
    ]
