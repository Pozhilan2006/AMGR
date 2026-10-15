"""SQLAlchemy ORM model for AMGR human approval requests.

Stores durable records of human approval requests, lifecycle transitions,
and resolution decisions following the Member 3 Phase 5 specifications.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.session import Base


class ApprovalModel(Base):
    """SQLAlchemy model representing a persistent human approval request."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_decision_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_payload_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)
    final_effect: Mapped[str] = mapped_column(String(32), default="REQUIRE_APPROVAL", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
