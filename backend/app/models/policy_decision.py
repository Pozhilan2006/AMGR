"""SQLAlchemy ORM model for authorization decision audit history.

Stores immutable records of runtime authorization evaluations separately from
editable policy definitions following the Member 3 Phase 1 roadmap requirements.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.session import Base


class PolicyDecisionModel(Base):
    """SQLAlchemy model representing an immutable runtime authorization decision record."""

    __tablename__ = "policy_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    matched_rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_rule_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    matched_policy_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_message: Mapped[str] = mapped_column(Text, nullable=False)
    context_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
