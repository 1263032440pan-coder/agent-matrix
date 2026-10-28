"""V1.6d: project.delivery_validator_hash + generated_at

Revision ID: d9f4a1b2c3e5
Revises: c8e1f2a3b4d5
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f4a1b2c3e5"
down_revision: Union[str, Sequence[str], None] = "c8e1f2a3b4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("delivery_validator_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "project",
        sa.Column(
            "delivery_validator_generated_at",
            sa.DateTime(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("project", "delivery_validator_generated_at")
    op.drop_column("project", "delivery_validator_hash")
