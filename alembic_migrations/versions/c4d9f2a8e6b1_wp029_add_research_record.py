"""WP-029 add research_record table

Revision ID: c4d9f2a8e6b1
Revises: b3c8e1f5a9d7
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9f2a8e6b1"
down_revision: Union[str, Sequence[str], None] = "b3c8e1f5a9d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("research_kind", sa.String(32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("findings_json", sa.Text(), nullable=True),
        sa.Column("gap_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="OPEN"),
        sa.Column("source_refs_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("question", "research_kind",
                            name="uq_research_q_kind"),
    )
    op.create_index("ix_research_project", "research_record", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_research_project", table_name="research_record")
    op.drop_table("research_record")
