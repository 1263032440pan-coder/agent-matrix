"""WP-023 completion_evidence table

Revision ID: b9c6e2f4a8d1
Revises: a8b4d6f1c3e7
Create Date: 2026-10-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c6e2f4a8d1"
down_revision: Union[str, Sequence[str], None] = "a8b4d6f1c3e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "completion_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("invalidated_reason", sa.String(512), nullable=True),
    )
    op.create_index("ix_evidence_project", "completion_evidence",
                    ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_project", table_name="completion_evidence")
    op.drop_table("completion_evidence")
