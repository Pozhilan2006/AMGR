"""Persist scoped observed flow events for lineage queries.

Revision ID: 0001_flow_events
Revises:
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_flow_events"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flow_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("parent_event_id", sa.Uuid(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_component_id", sa.String(length=200), nullable=False),
        sa.Column("source_component_type", sa.String(length=100), nullable=False),
        sa.Column("source_component_name", sa.String(length=200), nullable=True),
        sa.Column("destination_component_id", sa.String(length=200), nullable=False),
        sa.Column("destination_component_type", sa.String(length=100), nullable=False),
        sa.Column("destination_component_name", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=500), nullable=False),
        sa.Column("data_labels", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=100), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("event_id", "application_id", "environment"),
    )
    op.create_index(
        "ix_flow_events_scope_flow_time",
        "flow_events",
        ["application_id", "environment", "flow_id", "timestamp", "event_id"],
    )
    op.create_index(
        "ix_flow_events_scope_parent",
        "flow_events",
        ["application_id", "environment", "parent_event_id"],
    )
    op.create_index(
        "ix_flow_events_scope_source",
        "flow_events",
        ["application_id", "environment", "source_component_id"],
    )
    op.create_index(
        "ix_flow_events_scope_destination",
        "flow_events",
        ["application_id", "environment", "destination_component_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_flow_events_scope_destination", table_name="flow_events")
    op.drop_index("ix_flow_events_scope_source", table_name="flow_events")
    op.drop_index("ix_flow_events_scope_parent", table_name="flow_events")
    op.drop_index("ix_flow_events_scope_flow_time", table_name="flow_events")
    op.drop_table("flow_events")
