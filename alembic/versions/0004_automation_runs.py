"""Add automation run history.

Revision ID: 0004_automation_runs
Revises: 0003_x_reconciliation_timestamps
Create Date: 2026-05-02 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_automation_runs"
down_revision: str | None = "0003_x_reconciliation_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("created_drafts_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_drafts_count", sa.Integer(), nullable=False),
        sa.Column("auto_scheduled_count", sa.Integer(), nullable=False),
        sa.Column("approval_required_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("reconciled_count", sa.Integer(), nullable=False),
        sa.Column("metrics_collected_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_automation_runs_status", "automation_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_automation_runs_status", table_name="automation_runs")
    op.drop_table("automation_runs")
