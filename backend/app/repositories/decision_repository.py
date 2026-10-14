"""Authorization decision history repository for AMGR.

Persists and queries runtime evaluation decisions separately from editable
policy definitions following the Member 3 Phase 1 roadmap requirements.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
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

        context_dict: dict[str, Any] | None = None
        if isinstance(request.context, dict):
            context_dict = dict(request.context)
        elif request.context is not None:
            context_dict = {"raw_context": request.context}

        if decision.policy_version is not None:
            if context_dict is None:
                context_dict = {}
            context_dict["policy_version"] = decision.policy_version

        if decision.missing_attributes:
            if context_dict is None:
                context_dict = {}
            context_dict["missing_attributes"] = list(decision.missing_attributes)

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
            "context_data": context_dict,
            "evaluated_at": decision.evaluated_at,
        }

        if self.db_session is not None:
            try:
                model = PolicyDecisionModel(**record_data)
                self.db_session.add(model)
                self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise RuntimeError(
                    f"Failed to persist decision to database: {exc}"
                ) from exc

        self._decisions.append(record_data)
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
        policy_id: str | None = None,
        decision: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Returns decision records, optionally filtered by trace_id, policy_id, decision, action, and resource."""
        if self.db_session is not None:
            stmt = select(PolicyDecisionModel)
            if trace_id is not None:
                stmt = stmt.where(PolicyDecisionModel.trace_id == trace_id)
            if policy_id is not None:
                stmt = stmt.where(PolicyDecisionModel.policy_id == policy_id)
            if decision is not None:
                stmt = stmt.where(PolicyDecisionModel.decision == decision)
            if action is not None:
                stmt = stmt.where(PolicyDecisionModel.action == action)
            if resource is not None:
                stmt = stmt.where(PolicyDecisionModel.resource == resource)

            stmt = (
                stmt.order_by(
                    PolicyDecisionModel.evaluated_at.desc(),
                    PolicyDecisionModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
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

        filtered: list[dict[str, Any]] = []
        for d in self._decisions:
            if trace_id is not None and d["trace_id"] != trace_id:
                continue
            if policy_id is not None and d.get("policy_id") != policy_id:
                continue
            if decision is not None and d.get("decision") != decision:
                continue
            if action is not None and d.get("action") != action:
                continue
            if resource is not None and d.get("resource") != resource:
                continue
            filtered.append(d)

        sorted_decisions = sorted(
            filtered,
            key=lambda x: (x.get("evaluated_at") or datetime.min, x.get("id", "")),
            reverse=True,
        )
        return sorted_decisions[offset : offset + limit]

    def count_decisions(
        self,
        trace_id: str | None = None,
        policy_id: str | None = None,
        decision: str | None = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> int:
        """Returns total count of decision records matching filters."""
        if self.db_session is not None:
            stmt = select(func.count()).select_from(PolicyDecisionModel)
            if trace_id is not None:
                stmt = stmt.where(PolicyDecisionModel.trace_id == trace_id)
            if policy_id is not None:
                stmt = stmt.where(PolicyDecisionModel.policy_id == policy_id)
            if decision is not None:
                stmt = stmt.where(PolicyDecisionModel.decision == decision)
            if action is not None:
                stmt = stmt.where(PolicyDecisionModel.action == action)
            if resource is not None:
                stmt = stmt.where(PolicyDecisionModel.resource == resource)
            return int(self.db_session.scalar(stmt) or 0)

        count = 0
        for d in self._decisions:
            if trace_id is not None and d["trace_id"] != trace_id:
                continue
            if policy_id is not None and d.get("policy_id") != policy_id:
                continue
            if decision is not None and d.get("decision") != decision:
                continue
            if action is not None and d.get("action") != action:
                continue
            if resource is not None and d.get("resource") != resource:
                continue
            count += 1
        return count

    def get_decision_summary(
        self,
        trace_id: str | None = None,
        policy_id: str | None = None,
        decision: str | None = None,
        action: str | None = None,
        resource: str | None = None,
    ) -> dict[str, Any]:
        """Calculates aggregated metrics, outcome distributions, and time bounds for evaluated decisions."""
        if self.db_session is not None:
            stmt = select(PolicyDecisionModel)
            if trace_id is not None:
                stmt = stmt.where(PolicyDecisionModel.trace_id == trace_id)
            if policy_id is not None:
                stmt = stmt.where(PolicyDecisionModel.policy_id == policy_id)
            if decision is not None:
                stmt = stmt.where(PolicyDecisionModel.decision == decision)
            if action is not None:
                stmt = stmt.where(PolicyDecisionModel.action == action)
            if resource is not None:
                stmt = stmt.where(PolicyDecisionModel.resource == resource)

            records = self.db_session.scalars(stmt).all()
            total = len(records)
            decision_counts: dict[str, int] = {}
            reason_counts: dict[str, int] = {}
            policy_counts: dict[str, int] = {}
            action_counts: dict[str, int] = {}
            earliest_dt: datetime | None = None
            latest_dt: datetime | None = None

            for r in records:
                decision_counts[r.decision] = decision_counts.get(r.decision, 0) + 1
                reason_counts[r.reason_code] = reason_counts.get(r.reason_code, 0) + 1
                if r.policy_id:
                    policy_counts[r.policy_id] = policy_counts.get(r.policy_id, 0) + 1
                action_counts[r.action] = action_counts.get(r.action, 0) + 1

                if r.evaluated_at is not None:
                    if earliest_dt is None or r.evaluated_at < earliest_dt:
                        earliest_dt = r.evaluated_at
                    if latest_dt is None or r.evaluated_at > latest_dt:
                        latest_dt = r.evaluated_at

            return {
                "total_decisions": total,
                "decision_counts": decision_counts,
                "reason_code_counts": reason_counts,
                "policy_counts": policy_counts,
                "action_counts": action_counts,
                "earliest_evaluated_at": earliest_dt,
                "latest_evaluated_at": latest_dt,
            }

        decision_counts = {}
        reason_counts = {}
        policy_counts = {}
        action_counts = {}
        earliest_dt = None
        latest_dt = None
        matching_count = 0

        for d in self._decisions:
            if trace_id is not None and d["trace_id"] != trace_id:
                continue
            if policy_id is not None and d.get("policy_id") != policy_id:
                continue
            if decision is not None and d.get("decision") != decision:
                continue
            if action is not None and d.get("action") != action:
                continue
            if resource is not None and d.get("resource") != resource:
                continue

            matching_count += 1
            dec_val = d.get("decision", "UNKNOWN")
            decision_counts[dec_val] = decision_counts.get(dec_val, 0) + 1

            r_code = d.get("reason_code", "UNKNOWN")
            reason_counts[r_code] = reason_counts.get(r_code, 0) + 1

            pol = d.get("policy_id")
            if pol:
                policy_counts[pol] = policy_counts.get(pol, 0) + 1

            act = d.get("action", "UNKNOWN")
            action_counts[act] = action_counts.get(act, 0) + 1

            eval_time = d.get("evaluated_at")
            if eval_time is not None:
                if earliest_dt is None or eval_time < earliest_dt:
                    earliest_dt = eval_time
                if latest_dt is None or eval_time > latest_dt:
                    latest_dt = eval_time

        return {
            "total_decisions": matching_count,
            "decision_counts": decision_counts,
            "reason_code_counts": reason_counts,
            "policy_counts": policy_counts,
            "action_counts": action_counts,
            "earliest_evaluated_at": earliest_dt,
            "latest_evaluated_at": latest_dt,
        }

    def purge_decisions(
        self,
        before_timestamp: datetime | None = None,
        policy_id: str | None = None,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        """Purges decisions evaluated strictly before before_timestamp, optionally for a specific policy.

        Returns:
            A tuple of (purged_count, remaining_count).
        """
        if before_timestamp is None:
            raise ValueError("before_timestamp must be provided for audit retention purge.")

        if self.db_session is not None:
            try:
                count_stmt = select(func.count()).select_from(PolicyDecisionModel).where(
                    PolicyDecisionModel.evaluated_at < before_timestamp
                )
                if policy_id is not None:
                    count_stmt = count_stmt.where(PolicyDecisionModel.policy_id == policy_id)
                eligible_count = int(self.db_session.scalar(count_stmt) or 0)

                total_stmt = select(func.count()).select_from(PolicyDecisionModel)
                total_before = int(self.db_session.scalar(total_stmt) or 0)

                if dry_run or eligible_count == 0:
                    return eligible_count, total_before - (0 if dry_run else eligible_count)

                del_stmt = delete(PolicyDecisionModel).where(
                    PolicyDecisionModel.evaluated_at < before_timestamp
                )
                if policy_id is not None:
                    del_stmt = del_stmt.where(PolicyDecisionModel.policy_id == policy_id)

                self.db_session.execute(del_stmt)
                self.db_session.commit()

                remaining = int(self.db_session.scalar(total_stmt) or 0)
                return eligible_count, remaining
            except Exception as exc:
                self.db_session.rollback()
                raise RuntimeError(f"Failed to purge decisions from database: {exc}") from exc

        to_keep: list[dict[str, Any]] = []
        purged = 0
        for d in self._decisions:
            eval_time = d.get("evaluated_at")
            should_purge = False
            if eval_time is not None and eval_time < before_timestamp:
                if policy_id is None or d.get("policy_id") == policy_id:
                    should_purge = True

            if should_purge:
                purged += 1
            else:
                to_keep.append(d)

        if not dry_run:
            self._decisions = to_keep

        remaining_count = len(self._decisions) if not dry_run else len(self._decisions) - purged
        return purged, remaining_count

