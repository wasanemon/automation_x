"""Add LLM run history.

Revision ID: 0006_llm_runs
Revises: 0005_automation_run_counters
Create Date: 2026-05-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_llm_runs"
down_revision: str | None = "0005_automation_run_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=60), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("response_id", sa.String(length=160), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_runs_kind"), "llm_runs", ["kind"], unique=False)
    op.create_index(op.f("ix_llm_runs_status"), "llm_runs", ["status"], unique=False)
    op.add_column(
        "automation_runs",
        sa.Column("llm_generated_drafts_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("llm_hypotheses_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column("llm_skipped_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("automation_runs", "llm_skipped_count")
    op.drop_column("automation_runs", "llm_hypotheses_count")
    op.drop_column("automation_runs", "llm_generated_drafts_count")
    op.drop_index(op.f("ix_llm_runs_status"), table_name="llm_runs")
    op.drop_index(op.f("ix_llm_runs_kind"), table_name="llm_runs")
    op.drop_table("llm_runs")
