"""V1.6c: project.delivery_manifest JSON 列

Revision ID: c8e1f2a3b4d5
Revises: b7c4d9e1f2a3
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1f2a3b4d5"
down_revision: Union[str, Sequence[str], None] = "b7c4d9e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("delivery_manifest", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project", "delivery_manifest")
