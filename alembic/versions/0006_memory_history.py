"""Add operational memory history tables.

Revision ID: 0006_memory_history
Revises: 0005_automation_run_counters
Create Date: 2026-05-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_memory_history"
down_revision: str | None = "0005_automation_run_counters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idea_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("target_metric", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hypotheses_idea_id"), "hypotheses", ["idea_id"], unique=False)
    op.create_index(op.f("ix_hypotheses_source"), "hypotheses", ["source"], unique=False)
    op.create_index(op.f("ix_hypotheses_status"), "hypotheses", ["status"], unique=False)

    op.create_table(
        "draft_import_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("idea_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("input_context_json", sa.JSON(), nullable=False),
        sa.Column("hypotheses_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("imported_draft_ids_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["idea_id"], ["ideas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_draft_import_runs_idea_id"),
        "draft_import_runs",
        ["idea_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_draft_import_runs_source"),
        "draft_import_runs",
        ["source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_draft_import_runs_status"),
        "draft_import_runs",
        ["status"],
        unique=False,
    )

    op.add_column("drafts", sa.Column("hypothesis_id", sa.Integer(), nullable=True))
    op.add_column("drafts", sa.Column("draft_import_run_id", sa.Integer(), nullable=True))
    op.add_column(
        "drafts",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_foreign_key(
        op.f("fk_drafts_hypothesis_id_hypotheses"),
        "drafts",
        "hypotheses",
        ["hypothesis_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_drafts_draft_import_run_id_draft_import_runs"),
        "drafts",
        "draft_import_runs",
        ["draft_import_run_id"],
        ["id"],
    )

    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("automation_run_id", sa.Integer(), nullable=True),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=120), nullable=False),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["automation_run_id"], ["automation_runs.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_decision_logs_automation_run_id"),
        "decision_logs",
        ["automation_run_id"],
        unique=False,
    )
    op.create_index(op.f("ix_decision_logs_decision"), "decision_logs", ["decision"], unique=False)
    op.create_index(op.f("ix_decision_logs_draft_id"), "decision_logs", ["draft_id"], unique=False)
    op.create_index(op.f("ix_decision_logs_post_id"), "decision_logs", ["post_id"], unique=False)
    op.create_index(op.f("ix_decision_logs_stage"), "decision_logs", ["stage"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_decision_logs_stage"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_post_id"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_draft_id"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_decision"), table_name="decision_logs")
    op.drop_index(op.f("ix_decision_logs_automation_run_id"), table_name="decision_logs")
    op.drop_table("decision_logs")

    op.drop_constraint(op.f("fk_drafts_draft_import_run_id_draft_import_runs"), "drafts")
    op.drop_constraint(op.f("fk_drafts_hypothesis_id_hypotheses"), "drafts")
    op.drop_column("drafts", "metadata_json")
    op.drop_column("drafts", "draft_import_run_id")
    op.drop_column("drafts", "hypothesis_id")

    op.drop_index(op.f("ix_draft_import_runs_status"), table_name="draft_import_runs")
    op.drop_index(op.f("ix_draft_import_runs_source"), table_name="draft_import_runs")
    op.drop_index(op.f("ix_draft_import_runs_idea_id"), table_name="draft_import_runs")
    op.drop_table("draft_import_runs")

    op.drop_index(op.f("ix_hypotheses_status"), table_name="hypotheses")
    op.drop_index(op.f("ix_hypotheses_source"), table_name="hypotheses")
    op.drop_index(op.f("ix_hypotheses_idea_id"), table_name="hypotheses")
    op.drop_table("hypotheses")
