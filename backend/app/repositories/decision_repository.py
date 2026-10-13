"""Authorization decision history repository for AMGR.

Persists and queries runtime evaluation decisions separately from editable
policy definitions following the Member 3 Phase 1 roadmap requirements.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.policy_decision import PolicyDecisionModel
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)


class DecisionRepository:
    """Repository managing authorization decision history.

    Supports both database persistence (via SQLAlchemy Session) and in-memory fallback.
    """

    def __init__(self, db_session: Session | None = None) -> None:
        self.db_session = db_session
        self._decisions: list[dict[str, Any]] = []

    def record_decision(
        self,
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
    ) -> str:
        """Records an authorization decision into history.

        Returns:
            decision_id (unique string identifier for the decision record).
        """
        decision_id = f"dec_{uuid.uuid4().hex[:16]}"
        subj_str = (
            json.dumps(request.subject)
            if isinstance(request.subject, (dict, list))
            else str(request.subject)
        )

        record_data = {
            "id": decision_id,
            "trace_id": request.trace_id,
            "subject": subj_str,
            "action": request.action,
            "resource": request.resource,
            "decision": decision.decision.value,
            "policy_id": decision.policy_id,
            "matched_rule_id": decision.matched_rule_id,
            "matched_rule_ids": decision.matched_rule_ids,
            "matched_policy_ids": decision.matched_policy_ids,
            "reason_code": (
                decision.reason_code.value
                if hasattr(decision.reason_code, "value")
                else str(decision.reason_code)
            ),
            "reason_message": decision.reason_message,
            "context_data": request.context,
            "evaluated_at": decision.evaluated_at,
        }

        self._decisions.append(record_data)

        if self.db_session is not None:
            model = PolicyDecisionModel(**record_data)
            self.db_session.add(model)
            self.db_session.commit()

        return decision_id

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Retrieves a decision record by ID."""
        if self.db_session is not None:
            stmt = select(PolicyDecisionModel).where(PolicyDecisionModel.id == decision_id)
            obj = self.db_session.scalars(stmt).first()
            if obj is not None:
                return {
                    "id": obj.id,
                    "trace_id": obj.trace_id,
                    "subject": obj.subject,
                    "action": obj.action,
                    "resource": obj.resource,
                    "decision": obj.decision,
                    "policy_id": obj.policy_id,
                    "matched_rule_id": obj.matched_rule_id,
                    "matched_rule_ids": obj.matched_rule_ids,
                    "matched_policy_ids": obj.matched_policy_ids,
                    "reason_code": obj.reason_code,
                    "reason_message": obj.reason_message,
                    "context_data": obj.context_data,
                    "evaluated_at": obj.evaluated_at,
                }

        for d in self._decisions:
            if d["id"] == decision_id:
                return d
        return None

    def list_decisions(
        self,
        trace_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Returns decision records, optionally filtered by trace_id."""
        if self.db_session is not None:
            stmt = select(PolicyDecisionModel)
            if trace_id is not None:
                stmt = stmt.where(PolicyDecisionModel.trace_id == trace_id)
            stmt = stmt.order_by(PolicyDecisionModel.evaluated_at.desc()).limit(limit)
            results = self.db_session.scalars(stmt).all()
            return [
                {
                    "id": obj.id,
                    "trace_id": obj.trace_id,
                    "subject": obj.subject,
                    "action": obj.action,
                    "resource": obj.resource,
                    "decision": obj.decision,
                    "policy_id": obj.policy_id,
                    "matched_rule_id": obj.matched_rule_id,
                    "matched_rule_ids": obj.matched_rule_ids,
                    "matched_policy_ids": obj.matched_policy_ids,
                    "reason_code": obj.reason_code,
                    "reason_message": obj.reason_message,
                    "context_data": obj.context_data,
                    "evaluated_at": obj.evaluated_at,
                }
                for obj in results
            ]

        res = [d for d in self._decisions if trace_id is None or d["trace_id"] == trace_id]
        return res[-limit:]
