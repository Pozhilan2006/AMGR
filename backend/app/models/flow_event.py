"""Persisted scoped flow events and their optional graph references."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class FlowEventRecord(Base):
    """Database representation of the SDK event contract.

    ``event_id`` is unique within an application/environment scope, matching the
    composite key in the initial event migration. Component references remain in
    their original columns; the ``source`` and ``destination`` properties expose
    the structured contract expected by graph ingestion.
    """

    __tablename__ = "flow_events"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    environment: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)
    flow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    parent_event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    source_component_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_component_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    destination_component_id: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_component_type: Mapped[str] = mapped_column(String(100), nullable=False)
    destination_component_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    action: Mapped[str] = mapped_column(String(500), nullable=False)
    data_labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(100), nullable=False, default="observed")
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    source_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", name="fk_flow_events_source_node", ondelete="SET NULL"),
        nullable=True,
    )
    destination_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", name="fk_flow_events_destination_node", ondelete="SET NULL"),
        nullable=True,
    )
    edge_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_edges.edge_id", name="fk_flow_events_edge", ondelete="SET NULL"),
        nullable=True,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_flow_events_duration_nonnegative",
        ),
        Index("ix_flow_events_flow_id_timestamp", "flow_id", "timestamp"),
        Index("ix_flow_events_flow_id", "flow_id"),
        Index("ix_flow_events_correlation_id", "correlation_id"),
        Index("ix_flow_events_parent_event_id", "parent_event_id"),
        Index("ix_flow_events_timestamp", "timestamp"),
        Index("ix_flow_events_environment", "environment"),
        Index("ix_flow_events_source_node_id", "source_node_id"),
        Index("ix_flow_events_destination_node_id", "destination_node_id"),
        Index("ix_flow_events_edge_id", "edge_id"),
    )

    @property
    def source(self) -> dict[str, Any]:
        return {
            "component_id": self.source_component_id,
            "component_type": self.source_component_type,
            "name": self.source_component_name,
        }

    @source.setter
    def source(self, value: dict[str, Any]) -> None:
        self.source_component_id = value["component_id"]
        self.source_component_type = value["component_type"]
        self.source_component_name = value.get("name")

    @property
    def destination(self) -> dict[str, Any]:
        return {
            "component_id": self.destination_component_id,
            "component_type": self.destination_component_type,
            "name": self.destination_component_name,
        }

    @destination.setter
    def destination(self, value: dict[str, Any]) -> None:
        self.destination_component_id = value["component_id"]
        self.destination_component_type = value["component_type"]
        self.destination_component_name = value.get("name")

    @property
    def safe_metadata(self) -> dict[str, Any]:
        return self.event_metadata

    @safe_metadata.setter
    def safe_metadata(self, value: dict[str, Any]) -> None:
        self.event_metadata = value

    @property
    def metadata_json(self) -> dict[str, Any]:
        """Compatibility accessor used by the scoped repository interface."""
        return self.event_metadata

    @metadata_json.setter
    def metadata_json(self, value: dict[str, Any]) -> None:
        self.event_metadata = value

    def to_flow_event_dict(self) -> dict[str, Any]:
        """Return the public fields, omitting internal deferred-parent metadata."""
        parent_event_id = self.parent_event_id
        unresolved_parent_id = (self.event_metadata or {}).get("_unresolved_parent_id")
        if parent_event_id is None and unresolved_parent_id is not None:
            try:
                parent_event_id = UUID(str(unresolved_parent_id))
            except (ValueError, TypeError, AttributeError):
                parent_event_id = None
        return {
            "event_id": self.event_id,
            "application_id": self.application_id,
            "environment": self.environment,
            "flow_id": self.flow_id,
            "correlation_id": self.correlation_id,
            "parent_event_id": parent_event_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "destination": self.destination,
            "action": self.action,
            "data_labels": self.data_labels,
            "metadata": {
                key: value
                for key, value in (self.event_metadata or {}).items()
                if not (isinstance(key, str) and key.startswith("_"))
            },
            "status": self.status,
            "duration_ms": self.duration_ms,
        }

    def __repr__(self) -> str:
        return f"<FlowEventRecord(event_id={self.event_id}, scope={self.application_id}:{self.environment})>"


PersistedFlowEvent = FlowEventRecord
