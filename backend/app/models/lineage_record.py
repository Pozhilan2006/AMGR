"""SQLAlchemy model for lineage and provenance records in AMGR."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
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
    from .flow_event import FlowEventRecord
    from .graph_node import GraphNode


class LineageRecord(Base):
    """Represents a lineage transition and provenance evidence record in AMGR.

    Tracks causal relationships, component-to-component transitions, and
    data sensitivity labels across runtime execution flows.

    Lineage Classification Rules:
        - `directly_observed`: Point-to-point transition observed directly by SDK.
        - `explicitly_derived`: Causal link validated via parent_event_id DAG.
        - `unknown_or_incomplete`: Missing upstream parent or broken provenance chain.
        Missing lineage must never be fabricated; broken links are explicitly flagged.

    Metadata Policy:
        The `transformation_metadata` column stores ONLY sanitized transformation
        and provenance attributes. Raw prompts, credentials, secrets, and full
        payloads must NEVER be persisted here.

    Foreign Key & Deletion Policy:
        Event references include application and environment in their composite
        foreign keys so lineage cannot cross a scope. The originating event uses
        RESTRICT; optional graph-node references use SET NULL.
    """

    __tablename__ = "lineage_records"

    # Primary Key
    record_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        doc="Internal primary identifier for the lineage record.",
    )

    # Flow Scope
    flow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        doc="Runtime flow session identifier.",
    )

    # Scope columns are part of event identity and are repeated here so every
    # event relationship remains within the same application/environment.
    application_id: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(100), nullable=False)

    # Event References
    originating_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        doc="Foreign key to the originating FlowEvent that evidenced this transition.",
    )
    parent_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        default=None,
        doc="Optional foreign key to the causal parent event in flow_events.",
    )

    # Optional Graph Node Linkages
    source_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional foreign key to the origin GraphNode.",
    )
    destination_node_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("graph_nodes.node_id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        doc="Optional foreign key to the destination GraphNode.",
    )

    # Lineage Classification & Provenance Evidence
    lineage_classification: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Classification ('directly_observed', 'explicitly_derived', 'unknown_or_incomplete').",
    )
    evidence_source: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Source of lineage evidence (e.g., 'sdk_runtime_event', 'context_propagation').",
    )

    # Transformation Action
    action: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Action or transformation performed during this transition.",
    )

    # Sensitivity & Classification Labels
    data_labels: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        doc="List of sensitivity classification labels associated with this lineage step.",
    )

    # Safe Transformation Metadata
    transformation_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Sanitized transformation metadata (no raw prompts, secrets, or payloads).",
    )

    # Topological Distance / Depth
    depth: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc="Optional distance or topological depth from the root event in the flow.",
    )

    # Temporal Occurrence
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware UTC timestamp when this lineage record was recorded.",
    )

    # Relationships
    originating_event: Mapped["FlowEventRecord"] = relationship(
        "FlowEventRecord",
        foreign_keys=[originating_event_id, application_id, environment],
        doc="Relationship to the originating flow event.",
    )
    parent_event: Mapped["FlowEventRecord | None"] = relationship(
        "FlowEventRecord",
        foreign_keys=[parent_event_id, application_id, environment],
        doc="Relationship to the preceding parent flow event.",
    )
    source_node: Mapped["GraphNode | None"] = relationship(
        "GraphNode",
        foreign_keys=[source_node_id],
        doc="Relationship to the origin GraphNode.",
    )
    destination_node: Mapped["GraphNode | None"] = relationship(
        "GraphNode",
        foreign_keys=[destination_node_id],
        doc="Relationship to the destination GraphNode.",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["originating_event_id", "application_id", "environment"],
            ["flow_events.event_id", "flow_events.application_id", "flow_events.environment"],
            name="fk_lineage_originating_event_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_event_id", "application_id", "environment"],
            ["flow_events.event_id", "flow_events.application_id", "flow_events.environment"],
            name="fk_lineage_parent_event_scope",
        ),
        # Check constraint restricting classification
        CheckConstraint(
            "lineage_classification IN ('directly_observed', 'explicitly_derived', 'unknown_or_incomplete')",
            name="ck_lineage_records_classification",
        ),
        # Check constraint for nonnegative depth
        CheckConstraint(
            "depth IS NULL OR depth >= 0",
            name="ck_lineage_records_depth_nonnegative",
        ),
        # Indexes for query patterns
        Index("ix_lineage_records_flow_id", "flow_id"),
        Index("ix_lineage_records_originating_event_id", "originating_event_id"),
        Index("ix_lineage_records_parent_event_id", "parent_event_id"),
        Index("ix_lineage_records_classification", "lineage_classification"),
        Index("ix_lineage_records_source_node_id", "source_node_id"),
        Index("ix_lineage_records_destination_node_id", "destination_node_id"),
        Index("ix_lineage_records_flow_id_recorded_at", "flow_id", "recorded_at"),
    )

    @property
    def safe_metadata(self) -> dict[str, Any]:
        """Convenience property to access transformation_metadata."""
        return self.transformation_metadata

    @safe_metadata.setter
    def safe_metadata(self, value: dict[str, Any]) -> None:
        self.transformation_metadata = value

    def __repr__(self) -> str:
        return (
            f"<LineageRecord(record_id={self.record_id}, flow_id={self.flow_id}, "
            f"classification={self.lineage_classification!r}, action={self.action!r})>"
        )
