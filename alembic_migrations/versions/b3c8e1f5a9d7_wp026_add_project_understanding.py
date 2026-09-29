"""WP-026 add project_understanding table

Revision ID: b3c8e1f5a9d7
Revises: e7a1b5d9c4f2
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c8e1f5a9d7"
down_revision: Union[str, Sequence[str], None] = "e7a1b5d9c4f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_understanding",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("project.id"), nullable=False),
        sa.Column("domain_hypothesis", sa.String(256), nullable=False,
                  server_default="UNKNOWN"),
        sa.Column("project_type_hypothesis", sa.String(256), nullable=False,
                  server_default="UNKNOWN"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("known_facts_json", sa.Text(), nullable=True),
        sa.Column("assumptions_json", sa.Text(), nullable=True),
        sa.Column("open_questions_json", sa.Text(), nullable=True),
        sa.Column("known_requirements_json", sa.Text(), nullable=True),
        sa.Column("knowledge_gaps_json", sa.Text(), nullable=True),
        sa.Column("method_asset_hints_json", sa.Text(), nullable=True),
        sa.Column("analyzer_version", sa.String(32), nullable=False,
                  server_default="v0.1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pu_project", "project_understanding", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_pu_project", table_name="project_understanding")
    op.drop_table("project_understanding")
