"""add_approvals_table

Revision ID: 1252dd0605b1
Revises: 8460d43aeaff
Create Date: 2026-10-15 10:40:34.952551

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1252dd0605b1'
down_revision: Union[str, None] = '8460d43aeaff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("matched_rule_id", sa.String(length=64), nullable=True),
        sa.Column("original_decision_data", sa.JSON(), nullable=False),
        sa.Column("request_payload_data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("final_effect", sa.String(length=32), nullable=False, server_default="REQUIRE_APPROVAL"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_approvals_trace_id", "approvals", ["trace_id"])
    op.create_index("ix_approvals_policy_id", "approvals", ["policy_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])
    op.create_index("ix_approvals_expires_at", "approvals", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_approvals_expires_at", table_name="approvals")
    op.drop_index("ix_approvals_created_at", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_policy_id", table_name="approvals")
    op.drop_index("ix_approvals_trace_id", table_name="approvals")
    op.drop_table("approvals")
