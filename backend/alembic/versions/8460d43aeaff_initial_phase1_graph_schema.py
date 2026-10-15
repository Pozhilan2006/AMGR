"""Add graph and lineage tables to the existing scoped flow-event schema.

Revision ID: 8460d43aeaff
Revises: 20261011_0001, 0001_flow_events
Create Date: 2026-10-14 14:50:05.502522
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "8460d43aeaff"
down_revision: Union[str, tuple[str, str], None] = ("20261011_0001", "0001_flow_events")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    op.create_table(
        "graph_nodes",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("component_type", sa.String(length=100), nullable=False),
        sa.Column("external_component_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("application_id", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=100), server_default=sa.text("'development'"), nullable=False),
        sa.Column("metadata", json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("node_id"),
        sa.UniqueConstraint(
            "environment", "application_id", "component_type", "external_component_id",
            name="uq_graph_nodes_identity", postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_graph_nodes_component_type", "graph_nodes", ["component_type"])
    op.create_index("ix_graph_nodes_env_app", "graph_nodes", ["environment", "application_id"])
    op.create_index("ix_graph_nodes_external_component_id", "graph_nodes", ["external_component_id"])

    op.create_table(
        "graph_edges",
        sa.Column("edge_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("destination_node_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=500), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("total_events_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_status", sa.String(length=100), server_default=sa.text("'observed'"), nullable=False),
        sa.Column("data_labels", json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("metadata", json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint("total_events_count >= 1", name="ck_graph_edges_total_events_positive"),
        sa.ForeignKeyConstraint(["destination_node_id"], ["graph_nodes.node_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_node_id"], ["graph_nodes.node_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("edge_id"),
        sa.UniqueConstraint("source_node_id", "destination_node_id", "action", name="uq_graph_edges_connection"),
    )
    op.create_index("ix_graph_edges_action", "graph_edges", ["action"])
    op.create_index("ix_graph_edges_destination_node_id", "graph_edges", ["destination_node_id"])
    op.create_index("ix_graph_edges_last_observed_at", "graph_edges", ["last_observed_at"])
    op.create_index("ix_graph_edges_source_node_id", "graph_edges", ["source_node_id"])

    # flow_events is created by 0001_flow_events with a scoped composite primary key.
    # Keep that contract intact and add graph linkage columns in this revision.
    op.add_column("flow_events", sa.Column("source_node_id", sa.Uuid(), nullable=True))
    op.add_column("flow_events", sa.Column("destination_node_id", sa.Uuid(), nullable=True))
    op.add_column("flow_events", sa.Column("edge_id", sa.Uuid(), nullable=True))
    op.add_column(
        "flow_events",
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_check_constraint(
        "ck_flow_events_duration_nonnegative", "flow_events", "duration_ms IS NULL OR duration_ms >= 0"
    )
    op.create_foreign_key(
        "fk_flow_events_source_node", "flow_events", "graph_nodes", ["source_node_id"], ["node_id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_flow_events_destination_node", "flow_events", "graph_nodes", ["destination_node_id"], ["node_id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_flow_events_edge", "flow_events", "graph_edges", ["edge_id"], ["edge_id"], ondelete="SET NULL"
    )
    op.create_index("ix_flow_events_flow_id_timestamp", "flow_events", ["flow_id", "timestamp"])
    op.create_index("ix_flow_events_flow_id", "flow_events", ["flow_id"])
    op.create_index("ix_flow_events_correlation_id", "flow_events", ["correlation_id"])
    op.create_index("ix_flow_events_parent_event_id", "flow_events", ["parent_event_id"])
    op.create_index("ix_flow_events_timestamp", "flow_events", ["timestamp"])
    op.create_index("ix_flow_events_environment", "flow_events", ["environment"])
    op.create_index("ix_flow_events_source_node_id", "flow_events", ["source_node_id"])
    op.create_index("ix_flow_events_destination_node_id", "flow_events", ["destination_node_id"])
    op.create_index("ix_flow_events_edge_id", "flow_events", ["edge_id"])

    op.create_table(
        "lineage_records",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("originating_event_id", sa.Uuid(), nullable=False),
        sa.Column("parent_event_id", sa.Uuid(), nullable=True),
        sa.Column("source_node_id", sa.Uuid(), nullable=True),
        sa.Column("destination_node_id", sa.Uuid(), nullable=True),
        sa.Column("lineage_classification", sa.String(length=32), nullable=False),
        sa.Column("evidence_source", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=500), nullable=False),
        sa.Column("data_labels", json_type, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("transformation_metadata", json_type, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "lineage_classification IN ('directly_observed', 'explicitly_derived', 'unknown_or_incomplete')",
            name="ck_lineage_records_classification",
        ),
        sa.CheckConstraint("depth IS NULL OR depth >= 0", name="ck_lineage_records_depth_nonnegative"),
        sa.ForeignKeyConstraint(["destination_node_id"], ["graph_nodes.node_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_node_id"], ["graph_nodes.node_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["originating_event_id", "application_id", "environment"],
            ["flow_events.event_id", "flow_events.application_id", "flow_events.environment"],
            name="fk_lineage_originating_event_scope", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_event_id", "application_id", "environment"],
            ["flow_events.event_id", "flow_events.application_id", "flow_events.environment"],
            name="fk_lineage_parent_event_scope",
        ),
        sa.PrimaryKeyConstraint("record_id"),
    )
    op.create_index("ix_lineage_records_classification", "lineage_records", ["lineage_classification"])
    op.create_index("ix_lineage_records_destination_node_id", "lineage_records", ["destination_node_id"])
    op.create_index("ix_lineage_records_flow_id", "lineage_records", ["flow_id"])
    op.create_index("ix_lineage_records_flow_id_recorded_at", "lineage_records", ["flow_id", "recorded_at"])
    op.create_index("ix_lineage_records_originating_event_id", "lineage_records", ["originating_event_id"])
    op.create_index("ix_lineage_records_parent_event_id", "lineage_records", ["parent_event_id"])
    op.create_index("ix_lineage_records_source_node_id", "lineage_records", ["source_node_id"])


def downgrade() -> None:
    op.drop_index("ix_lineage_records_source_node_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_parent_event_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_originating_event_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_flow_id_recorded_at", table_name="lineage_records")
    op.drop_index("ix_lineage_records_flow_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_destination_node_id", table_name="lineage_records")
    op.drop_index("ix_lineage_records_classification", table_name="lineage_records")
    op.drop_table("lineage_records")

    for name in (
        "ix_flow_events_edge_id", "ix_flow_events_destination_node_id", "ix_flow_events_source_node_id",
        "ix_flow_events_environment", "ix_flow_events_timestamp", "ix_flow_events_parent_event_id",
        "ix_flow_events_correlation_id", "ix_flow_events_flow_id", "ix_flow_events_flow_id_timestamp",
    ):
        op.drop_index(name, table_name="flow_events")
    op.drop_constraint("fk_flow_events_edge", "flow_events", type_="foreignkey")
    op.drop_constraint("fk_flow_events_destination_node", "flow_events", type_="foreignkey")
    op.drop_constraint("fk_flow_events_source_node", "flow_events", type_="foreignkey")
    op.drop_constraint("ck_flow_events_duration_nonnegative", "flow_events", type_="check")
    op.drop_column("flow_events", "ingested_at")
    op.drop_column("flow_events", "edge_id")
    op.drop_column("flow_events", "destination_node_id")
    op.drop_column("flow_events", "source_node_id")

    op.drop_index("ix_graph_edges_source_node_id", table_name="graph_edges")
    op.drop_index("ix_graph_edges_last_observed_at", table_name="graph_edges")
    op.drop_index("ix_graph_edges_destination_node_id", table_name="graph_edges")
    op.drop_index("ix_graph_edges_action", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_external_component_id", table_name="graph_nodes")
    op.drop_index("ix_graph_nodes_env_app", table_name="graph_nodes")
    op.drop_index("ix_graph_nodes_component_type", table_name="graph_nodes")
    op.drop_table("graph_nodes")

