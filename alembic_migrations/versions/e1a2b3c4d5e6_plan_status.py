"""交付认领：project.plan_status

Revision ID: e1a2b3c4d5e6
Revises: d9f4a1b2c3e5
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "d9f4a1b2c3e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("plan_status", sa.String(16), nullable=True),
    )
    # 既有项目视为可执行
    op.execute("UPDATE project SET plan_status = 'READY' WHERE plan_status IS NULL")


def downgrade() -> None:
    op.drop_column("project", "plan_status")
