"""SQLAlchemy model for persisted runtime flow events in AMGR."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base

if TYPE_CHECKING:
    from .graph_edge import GraphEdge
    from .graph_node import GraphNode


class FlowEventRecord(Base):
    """Persisted representation of runtime flow events in AMGR.

    Preserves the shared FlowEvent contract semantics (event_id, flow_id,
    correlation_id, parent_event_id, timestamp, source, destination, action,
    data_labels, metadata, status, duration_ms) while adding internal graph
    references and audit fields.

    Deduplication Policy:
        The `event_id` serves as the primary deduplication key. A duplicate
        event ID must never silently overwrite a previously persisted event.

    Metadata Policy:
        The `event_metadata` attribute (mapped to column "metadata" to avoid
        shadowing DeclarativeBase.metadata) must store ONLY sanitized,
        non-sensitive attributes. Raw prompts, credentials, API secrets,
        or full payloads must NEVER be persisted here.

    ComponentRef Persistence:
        `source` and `destination` preserve the complete ComponentRef contract
        as structured JSON/JSONB objects ({component_id, component_type, name}).
    """

    __tablename__ = "flow_events"

    # Primary Key (supplied by incoming FlowEvent.event_id)
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        doc="Unique identifier for this event, matching the public FlowEvent contract.",
    )

    # Correlation Identifiers
    flow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        doc="Correlation key for the runtime flow session as a whole.",
    )
    correlation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        default=None,
        doc="Optional correlated request or session identifier.",
    )
    parent_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("flow_events.event_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional reference to initiating parent event for causal lineage.",
    )

    # Temporal Occurrence
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware UTC timestamp when the event occurred.",
    )

    # Complete ComponentRef Contracts (JSON/JSONB preservation)
    source: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        doc="Origin component reference preserving ComponentRef contract ({component_id, component_type, name}).",
    )
    destination: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        doc="Target component reference preserving ComponentRef contract ({component_id, component_type, name}).",
    )

    # Action and Classifications
    action: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Operation or event label carried by the event.",
    )
    data_labels: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        doc="List of classification labels describing sensitivity or data properties.",
    )

    # Safe Metadata (mapped to db column 'metadata' without colliding with DeclarativeBase.metadata)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Sanitized free-form event metadata (no raw prompts, secrets, or payloads).",
    )

    # Status and Duration
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="observed",
        server_default=text("'observed'"),
        doc="Business or processing status, such as 'observed' or 'blocked'.",
    )
    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        default=None,
        doc="Optional measured duration in milliseconds (nonnegative).",
    )

    # Internal Graph Linkages (Optional Foreign Keys)
    source_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional foreign key linking to resolved origin GraphNode.",
    )
    destination_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional foreign key linking to resolved destination GraphNode.",
    )
    edge_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_edges.edge_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional foreign key linking to resolved logical GraphEdge.",
    )

    # Audit & Environment Scoping
    environment: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="development",
        server_default=text("'development'"),
        doc="Component environment scope (e.g., development, staging, production).",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware UTC timestamp recording when AMGR persisted the event.",
    )

    # Relationships
    parent_event: Mapped["FlowEventRecord | None"] = relationship(
        "FlowEventRecord",
        remote_side=[event_id],
        foreign_keys=[parent_event_id],
        doc="Self-referential relationship to the initiating parent event.",
    )
    source_node: Mapped["GraphNode | None"] = relationship(
        "GraphNode",
        foreign_keys=[source_node_id],
        doc="Resolved source GraphNode.",
    )
    destination_node: Mapped["GraphNode | None"] = relationship(
        "GraphNode",
        foreign_keys=[destination_node_id],
        doc="Resolved destination GraphNode.",
    )
    edge: Mapped["GraphEdge | None"] = relationship(
        "GraphEdge",
        foreign_keys=[edge_id],
        doc="Resolved logical GraphEdge.",
    )

    __table_args__ = (
        # Nonnegative duration constraint
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_flow_events_duration_nonnegative",
        ),
        # Indexes for query patterns
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
    def safe_metadata(self) -> dict[str, Any]:
        """Convenience property to access event_metadata as safe_metadata."""
        return self.event_metadata

    @safe_metadata.setter
    def safe_metadata(self, value: dict[str, Any]) -> None:
        self.event_metadata = value

    def to_flow_event_dict(self) -> dict[str, Any]:
        """Export fields matching the public FlowEvent contract."""
        raw_meta = self.event_metadata
        if isinstance(raw_meta, dict):
            filtered_meta = {
                k: v for k, v in raw_meta.items() if not (isinstance(k, str) and k.startswith("_"))
            }
        else:
            filtered_meta = {}

        return {
            "event_id": self.event_id,
            "flow_id": self.flow_id,
            "correlation_id": self.correlation_id,
            "parent_event_id": self.parent_event_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "destination": self.destination,
            "action": self.action,
            "data_labels": self.data_labels,
            "metadata": filtered_meta,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }

    def __repr__(self) -> str:
        return (
            f"<FlowEventRecord(event_id={self.event_id}, flow_id={self.flow_id}, "
            f"action={self.action!r}, status={self.status!r})>"
        )


# Aliases for architectural clarity
PersistedFlowEvent = FlowEventRecord
FlowEvent = FlowEventRecord
