"""SQLAlchemy model for runtime graph nodes in AMGR."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, JSON, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class GraphNode(Base):
    """Represents an observed runtime component node within the AMGR flow graph.

    Identity Strategy:
        Nodes are identified across restarts by the natural key tuple:
        (environment, application_id, component_type, external_component_id).

        In PostgreSQL 15+, the uniqueness constraint uses NULLS NOT DISTINCT
        so that components where application_id is NULL/unspecified are still
        uniquely constrained per environment and component identifier.

    Metadata Policy:
        The `metadata` column (accessed via the `node_metadata` attribute to
        avoid shadowing SQLAlchemy's DeclarativeBase.metadata) must store ONLY
        safe, non-sensitive runtime attributes (e.g., version, runtime host, framework).
        Raw prompts, secrets, tokens, and payloads must NEVER be persisted here.
    """

    __tablename__ = "graph_nodes"

    # Internal Primary Key
    node_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        doc="Internal stable primary identifier for the graph node.",
    )

    # Component Categorization and Identity
    component_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Component category (e.g., agent, model, api, db, tool).",
    )
    external_component_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Stable external component identifier supplied by SDK runtime instrumentation.",
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Optional human-readable label or display name.",
    )

    # Scoping
    application_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Optional application boundary or service group identifier.",
    )
    environment: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="development",
        server_default=text("'development'"),
        doc="Deployment environment scope (e.g., development, staging, production).",
    )

    # Safe Metadata (mapped to db column "metadata" without colliding with DeclarativeBase.metadata)
    node_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Sanitized component metadata (no raw prompts, secrets, or payloads).",
    )

    # Observation Timestamps (Timezone-Aware UTC)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        doc="Timezone-aware UTC timestamp when this node was first observed.",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        doc="Timezone-aware UTC timestamp when this node was most recently observed.",
    )

    __table_args__ = (
        # Natural key constraint with NULLS NOT DISTINCT for PostgreSQL 15+
        UniqueConstraint(
            "environment",
            "application_id",
            "component_type",
            "external_component_id",
            name="uq_graph_nodes_identity",
            postgresql_nulls_not_distinct=True,
        ),
        # Indexes for common query patterns
        Index("ix_graph_nodes_component_type", "component_type"),
        Index("ix_graph_nodes_env_app", "environment", "application_id"),
        Index("ix_graph_nodes_external_component_id", "external_component_id"),
    )

    @property
    def safe_metadata(self) -> dict[str, Any]:
        """Convenience property to access node_metadata as safe_metadata."""
        return self.node_metadata

    @safe_metadata.setter
    def safe_metadata(self, value: dict[str, Any]) -> None:
        self.node_metadata = value

    def __repr__(self) -> str:
        return (
            f"<GraphNode(node_id={self.node_id}, type={self.component_type!r}, "
            f"external_id={self.external_component_id!r}, env={self.environment!r})>"
        )
