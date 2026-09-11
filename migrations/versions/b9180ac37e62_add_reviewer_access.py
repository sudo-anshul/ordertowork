"""Separate protected reviewer access and usage from public demo quotas.

Revision ID: b9180ac37e62
Revises: fe39217560ad
"""

import sqlalchemy as sa
from alembic import op

revision = "b9180ac37e62"
down_revision = "fe39217560ad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("reviewer_token_hash", sa.String(64), nullable=True))
    op.add_column("workspaces", sa.Column("reviewer_token_hash", sa.String(64), nullable=True))
    op.add_column(
        "agent_daily_usage",
        sa.Column("reviewer_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "nonnegative_reviewer_attempts", "agent_daily_usage", "reviewer_attempts >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("nonnegative_reviewer_attempts", "agent_daily_usage", type_="check")
    op.drop_column("agent_daily_usage", "reviewer_attempts")
    op.drop_column("workspaces", "reviewer_token_hash")
    op.drop_column("auth_sessions", "reviewer_token_hash")
