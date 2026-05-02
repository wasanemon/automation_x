"""Add X reconciliation timestamps.

Revision ID: 0003_x_reconciliation_timestamps
Revises: 0002_post_dry_run_idempotency
Create Date: 2026-05-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_x_reconciliation_timestamps"
down_revision: str | None = "0002_post_dry_run_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("x_post_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "posts",
        sa.Column("x_reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("posts", "x_reconciled_at")
    op.drop_column("posts", "x_post_created_at")
