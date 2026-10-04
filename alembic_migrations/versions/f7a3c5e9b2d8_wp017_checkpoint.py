"""WP-017 checkpoint_record table

Revision ID: f7a3c5e9b2d8
Revises: e6f2b4d8a1c9
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7a3c5e9b2d8"
down_revision: Union[str, Sequence[str], None] = "e6f2b4d8a1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checkpoint_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False,
                  server_default="MANUAL"),
        sa.Column("schema_version", sa.String(16), nullable=False,
                  server_default="v1"),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cp_project", "checkpoint_record", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_cp_project", table_name="checkpoint_record")
    op.drop_table("checkpoint_record")
