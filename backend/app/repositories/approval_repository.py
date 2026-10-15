"""Approval request repository for AMGR.

Persists and queries human approval requests supporting both SQLAlchemy database
persistence and in-memory operation following the Member 3 Phase 5 roadmap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.approval import ApprovalModel
from backend.app.schemas.approval import ApprovalRequest, ApprovalStatus
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from backend.app.schemas.policy_enums import PolicyEffect


class ApprovalPersistenceError(RuntimeError):
    """Raised when an approval database persistence operation fails and is rolled back."""
    pass


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensures a datetime is timezone-aware with UTC timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ApprovalRepository:
    """Repository managing human approval request persistence.

    Supports dual-mode operation:
    - Database mode: writes to SQLAlchemy Session and hydrates into memory cache.
    - In-memory mode: operates against an in-memory dictionary for tests without DB.
    """

    def __init__(self, db_session: Session | None = None) -> None:
        self.db_session = db_session
        self._requests: dict[str, ApprovalRequest] = {}

        if self.db_session is not None:
            self._load_from_db()

    def _load_from_db(self) -> None:
        """Hydrates repository cache from the database."""
        if self.db_session is None:
            return

        stmt = select(ApprovalModel).order_by(ApprovalModel.created_at.asc())
        db_records = self.db_session.scalars(stmt).all()
        for record in db_records:
            req = self._model_to_schema(record)
            self._requests[req.id] = req

    def _model_to_schema(self, model: ApprovalModel) -> ApprovalRequest:
        """Converts an ApprovalModel database record to an ApprovalRequest schema."""
        original_decision = AuthorizationDecision.model_validate(model.original_decision_data)
        request_payload = AuthorizationRequest.model_validate(model.request_payload_data)

        return ApprovalRequest(
            id=model.id,
            trace_id=model.trace_id,
            policy_id=model.policy_id,
            policy_version=model.policy_version,
            matched_rule_id=model.matched_rule_id,
            original_decision=original_decision,
            request_payload=request_payload,
            status=ApprovalStatus(model.status),
            created_at=_ensure_utc(model.created_at) or datetime.now(timezone.utc),
            expires_at=_ensure_utc(model.expires_at),
            decided_at=_ensure_utc(model.decided_at),
            decided_by=model.decided_by,
            decision_reason=model.decision_reason,
            final_effect=PolicyEffect(model.final_effect),
        )

    def _schema_to_model(self, req: ApprovalRequest) -> ApprovalModel:
        """Converts an ApprovalRequest schema to an ApprovalModel database record."""
        return ApprovalModel(
            id=req.id,
            trace_id=req.trace_id,
            policy_id=req.policy_id,
            policy_version=req.policy_version,
            matched_rule_id=req.matched_rule_id,
            original_decision_data=req.original_decision.model_dump(mode="json"),
            request_payload_data=req.request_payload.model_dump(mode="json"),
            status=req.status.value,
            final_effect=req.final_effect.value,
            created_at=req.created_at,
            expires_at=req.expires_at,
            decided_at=req.decided_at,
            decided_by=req.decided_by,
            decision_reason=req.decision_reason,
        )

    def _update_model_from_schema(self, model: ApprovalModel, req: ApprovalRequest) -> None:
        """Updates mutable lifecycle fields of an existing ApprovalModel record."""
        model.status = req.status.value
        model.final_effect = req.final_effect.value
        model.expires_at = req.expires_at
        model.decided_at = req.decided_at
        model.decided_by = req.decided_by
        model.decision_reason = req.decision_reason

    def save(self, req: ApprovalRequest) -> ApprovalRequest:
        """Saves or updates an approval request.

        In database mode, performs transactional insert/update and commits.
        If a database failure occurs, rolls back the transaction and raises ApprovalPersistenceError.
        """
        if self.db_session is not None:
            try:
                db_model = self.db_session.get(ApprovalModel, req.id)
                if db_model is None:
                    db_model = self._schema_to_model(req)
                    self.db_session.add(db_model)
                else:
                    self._update_model_from_schema(db_model, req)
                self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise ApprovalPersistenceError(
                    f"Failed to persist approval request '{req.id}' to database: {exc}"
                ) from exc

        self._requests[req.id] = req
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Retrieves an approval request by identifier.

        Checks in-memory cache first, then database if cache miss occurs.
        """
        if approval_id in self._requests:
            return self._requests[approval_id]

        if self.db_session is not None:
            db_model = self.db_session.get(ApprovalModel, approval_id)
            if db_model is not None:
                req = self._model_to_schema(db_model)
                self._requests[req.id] = req
                return req

        return None

    def delete(self, approval_id: str) -> bool:
        """Deletes an approval request by identifier.

        Used for transactional rollback and phantom approval cleanup.
        """
        self._requests.pop(approval_id, None)

        if self.db_session is not None:
            try:
                db_model = self.db_session.get(ApprovalModel, approval_id)
                if db_model is not None:
                    self.db_session.delete(db_model)
                    self.db_session.commit()
                    return True
                return False
            except Exception as exc:
                self.db_session.rollback()
                raise ApprovalPersistenceError(
                    f"Failed to delete approval request '{approval_id}' from database: {exc}"
                ) from exc

        return True

    def list_approvals(
        self,
        status_filter: ApprovalStatus | None = None,
        trace_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """Lists approval requests with filtering and pagination."""
        if self.db_session is not None:
            stmt = select(ApprovalModel)
            if status_filter is not None:
                stmt = stmt.where(ApprovalModel.status == status_filter.value)
            if trace_id is not None:
                stmt = stmt.where(ApprovalModel.trace_id == trace_id)
            if policy_id is not None:
                stmt = stmt.where(ApprovalModel.policy_id == policy_id)

            stmt = stmt.order_by(ApprovalModel.created_at.desc(), ApprovalModel.id.desc())
            stmt = stmt.offset(offset).limit(limit)

            records = self.db_session.scalars(stmt).all()
            return [self._model_to_schema(r) for r in records]

        # In-memory fallback
        results = list(self._requests.values())
        if status_filter is not None:
            results = [r for r in results if r.status == status_filter]
        if trace_id is not None:
            results = [r for r in results if r.trace_id == trace_id]
        if policy_id is not None:
            results = [r for r in results if r.policy_id == policy_id]

        results.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return results[offset : offset + limit]

    def list_all(self) -> list[ApprovalRequest]:
        """Returns all approval requests from repository."""
        if self.db_session is not None:
            stmt = select(ApprovalModel).order_by(ApprovalModel.created_at.desc())
            records = self.db_session.scalars(stmt).all()
            return [self._model_to_schema(r) for r in records]
        return list(self._requests.values())
