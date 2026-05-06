"""Add detailed automation run counters.

Revision ID: 0005_automation_run_counters
Revises: 0004_automation_runs
Create Date: 2026-05-02 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_automation_run_counters"
down_revision: str | None = "0004_automation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automation_runs",
        sa.Column("auto_posting_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "automation_runs",
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "automation_runs",
        sa.Column(
            "auto_schedule_candidates_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "automation_runs",
        sa.Column("dry_run_scheduled_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("live_scheduled_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("duplicate_skipped_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("frequency_limited_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("metrics_skipped_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("errors_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("automation_runs", "metadata_json")
    op.drop_column("automation_runs", "errors_json")
    op.drop_column("automation_runs", "metrics_skipped_count")
    op.drop_column("automation_runs", "frequency_limited_count")
    op.drop_column("automation_runs", "duplicate_skipped_count")
    op.drop_column("automation_runs", "live_scheduled_count")
    op.drop_column("automation_runs", "dry_run_scheduled_count")
    op.drop_column("automation_runs", "auto_schedule_candidates_count")
    op.drop_column("automation_runs", "kill_switch_active")
    op.drop_column("automation_runs", "auto_posting_enabled")
