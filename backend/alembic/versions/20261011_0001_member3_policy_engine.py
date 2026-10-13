"""Member 3 policy engine initial schema

Revision ID: 20261011_0001
Revises: 
Create Date: 2026-10-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20261011_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create policies table
    op.create_table(
        "policies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolution_algorithm", sa.String(length=32), nullable=False, server_default="BLOCK_OVERRIDES"),
        sa.Column("default_effect", sa.String(length=32), nullable=False, server_default="BLOCK"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rules_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policies_priority", "policies", ["priority"])
    op.create_index("ix_policies_enabled", "policies", ["enabled"])

    # 2. Create policy_versions table
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("policy_id", sa.String(length=64), sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rules_data", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_version"),
    )
    op.create_index("ix_policy_versions_policy_id", "policy_versions", ["policy_id"])
    op.create_index("ix_policy_versions_version", "policy_versions", ["version"])

    # 3. Create policy_decisions table (separate decision-history records)
    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=256), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=True),
        sa.Column("matched_rule_id", sa.String(length=64), nullable=True),
        sa.Column("matched_rule_ids", sa.JSON(), nullable=False),
        sa.Column("matched_policy_ids", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_message", sa.Text(), nullable=False),
        sa.Column("context_data", sa.JSON(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policy_decisions_trace_id", "policy_decisions", ["trace_id"])
    op.create_index("ix_policy_decisions_action", "policy_decisions", ["action"])
    op.create_index("ix_policy_decisions_resource", "policy_decisions", ["resource"])
    op.create_index("ix_policy_decisions_decision", "policy_decisions", ["decision"])
    op.create_index("ix_policy_decisions_policy_id", "policy_decisions", ["policy_id"])
    op.create_index("ix_policy_decisions_evaluated_at", "policy_decisions", ["evaluated_at"])


def downgrade() -> None:
    op.drop_table("policy_decisions")
    op.drop_table("policy_versions")
    op.drop_table("policies")
