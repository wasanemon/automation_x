"""Add post dry-run marker and draft idempotency.

Revision ID: 0002_post_dry_run_idempotency
Revises: 0001_initial
Create Date: 2026-05-01 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_post_dry_run_idempotency"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_unique_constraint("uq_posts_draft_id", "posts", ["draft_id"])
    op.alter_column("posts", "dry_run", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_posts_draft_id", "posts", type_="unique")
    op.drop_column("posts", "dry_run")
