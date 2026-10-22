"""V1.6 序1: project.delivery_profile 列

Revision ID: b7c4d9e1f2a3
Revises: e3e2cb299ef0
Create Date: 2026-10-22

交付画像（delivery_classifier 语义判定结果）——SOFTWARE_DEV | NOT_SOFTWARE |
NULL(未判定, 语义同 NOT_SOFTWARE)。纯事实记录: 决定 skill《交付基线》注入与否。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c4d9e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'e3e2cb299ef0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'project',
        sa.Column('delivery_profile', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('project', 'delivery_profile')
