"""WP-021 add project_summary table

Revision ID: e7a1b5d9c4f2
Revises: d9f3c2e8b7a4
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1b5d9c4f2"
down_revision: Union[str, Sequence[str], None] = "d9f3c2e8b7a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_summary",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(256), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_facts_json", sa.Text(), nullable=True),
        sa.Column("key_decisions_json", sa.Text(), nullable=True),
        sa.Column("open_issues_json", sa.Text(), nullable=True),
        sa.Column("task_refs_json", sa.Text(), nullable=True),
        sa.Column("artifact_refs_json", sa.Text(), nullable=True),
        sa.Column("evidence_refs_json", sa.Text(), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(8), nullable=False, server_default="STALE"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_summary_scope", "project_summary",
        ["project_id", "scope_type", "scope_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_summary_scope", table_name="project_summary")
    op.drop_table("project_summary")
