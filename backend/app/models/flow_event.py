from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Float, Index, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FlowEventRecord(Base):
    __tablename__ = "flow_events"
    __table_args__ = (
        Index(
            "ix_flow_events_scope_flow_time",
            "application_id",
            "environment",
            "flow_id",
            "timestamp",
            "event_id",
        ),
        Index(
            "ix_flow_events_scope_parent",
            "application_id",
            "environment",
            "parent_event_id",
        ),
        Index(
            "ix_flow_events_scope_source",
            "application_id",
            "environment",
            "source_component_id",
        ),
        Index(
            "ix_flow_events_scope_destination",
            "application_id",
            "environment",
            "destination_component_id",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(200), primary_key=True, nullable=False
    )
    environment: Mapped[str] = mapped_column(
        String(100), primary_key=True, nullable=False
    )
    flow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    parent_event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_component_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_component_name: Mapped[str | None] = mapped_column(String(200))
    destination_component_id: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    destination_component_name: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(500), nullable=False)
    data_labels: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float)
