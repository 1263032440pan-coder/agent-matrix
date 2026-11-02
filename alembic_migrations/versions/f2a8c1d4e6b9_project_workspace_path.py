"""project.workspace_path：创建时指定用户工作区

Revision ID: f2a8c1d4e6b9
Revises: e1a2b3c4d5e6
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a8c1d4e6b9"
down_revision: Union[str, Sequence[str], None] = "e1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("workspace_path", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project", "workspace_path")
