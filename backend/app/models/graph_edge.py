"""SQLAlchemy model for runtime graph edges in AMGR."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base

if TYPE_CHECKING:
    from .graph_node import GraphNode


class GraphEdge(Base):
    """Represents a logical directed interaction between two graph nodes in AMGR.

    Identity & Deduplication Strategy:
        Logical edges represent communication pathways between nodes for a given action.
        The natural unique key is (source_node_id, destination_node_id, action).
        Repeated observations of the same directed interaction update this aggregate
        record (incrementing `total_events_count`, advancing `last_observed_at`,
        updating `last_status`, and merging `data_labels`) rather than creating
        duplicate logical edges.

    Metadata Policy:
        The `edge_metadata` attribute (mapped to column "metadata" to avoid collision
        with DeclarativeBase.metadata) must store ONLY sanitized, non-sensitive edge
        attributes (e.g., protocol, aggregated performance stats, error codes).
        Raw prompts, credentials, API secrets, or full payloads must NEVER be persisted.

    Foreign Key & Deletion Policy:
        Foreign keys reference `graph_nodes.node_id` with `ondelete="RESTRICT"` to
        prevent deletion of active component nodes while logical edges depend on them.
    """

    __tablename__ = "graph_edges"

    # Internal Primary Key
    edge_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        doc="Internal stable primary identifier for the logical graph edge.",
    )

    # Node References
    source_node_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="RESTRICT"),
        nullable=False,
        doc="Foreign key referencing the origin/calling GraphNode.",
    )
    destination_node_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="RESTRICT"),
        nullable=False,
        doc="Foreign key referencing the target/callee GraphNode.",
    )

    # Interaction Action
    action: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Operation or action name representing this directed interaction.",
    )

    # Observation Timestamps (Timezone-Aware UTC)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware UTC timestamp when this directed interaction was first observed.",
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        doc="Timezone-aware UTC timestamp when this directed interaction was most recently observed.",
    )

    # Aggregate Observation Counter (Positive Integer, Initially 1)
    total_events_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        doc="Cumulative count of individual events observed traversing this logical edge.",
    )

    # Latest Event Status
    last_status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="observed",
        server_default=text("'observed'"),
        doc="Latest observed event status (consistent with FlowEvent.status, e.g., 'observed', 'blocked').",
    )

    # Classification & Sensitivity Labels (JSON/JSONB list)
    data_labels: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        doc="Aggregated list of distinct data sensitivity classification labels observed on this edge.",
    )

    # Safe Metadata (mapped to db column 'metadata' without colliding with DeclarativeBase.metadata)
    edge_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Sanitized edge metadata (no raw prompts, secrets, or payloads).",
    )

    # SQLAlchemy ORM Relationships
    source_node: Mapped["GraphNode"] = relationship(
        "GraphNode",
        foreign_keys=[source_node_id],
        doc="Relationship to the origin GraphNode.",
    )
    destination_node: Mapped["GraphNode"] = relationship(
        "GraphNode",
        foreign_keys=[destination_node_id],
        doc="Relationship to the recipient GraphNode.",
    )

    __table_args__ = (
        # Logical edge identity uniqueness
        UniqueConstraint(
            "source_node_id",
            "destination_node_id",
            "action",
            name="uq_graph_edges_connection",
        ),
        # Ensure count is always positive
        CheckConstraint(
            "total_events_count >= 1",
            name="ck_graph_edges_total_events_positive",
        ),
        # Query indexes
        Index("ix_graph_edges_source_node_id", "source_node_id"),
        Index("ix_graph_edges_destination_node_id", "destination_node_id"),
        Index("ix_graph_edges_action", "action"),
        Index("ix_graph_edges_last_observed_at", "last_observed_at"),
    )

    @property
    def safe_metadata(self) -> dict[str, Any]:
        """Convenience property to access edge_metadata as safe_metadata."""
        return self.edge_metadata

    @safe_metadata.setter
    def safe_metadata(self, value: dict[str, Any]) -> None:
        self.edge_metadata = value

    def __repr__(self) -> str:
        return (
            f"<GraphEdge(edge_id={self.edge_id}, source={self.source_node_id}, "
            f"destination={self.destination_node_id}, action={self.action!r}, count={self.total_events_count})>"
        )
