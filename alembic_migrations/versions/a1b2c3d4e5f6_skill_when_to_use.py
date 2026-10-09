"""Add when_to_use to skill_definition (LLM skill selection metadata).

Revision ID: a1b2c3d4e5f6
Revises: b9c6e2f4a8d1
Create Date: 2026-10-09
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "b9c6e2f4a8d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_definition",
        sa.Column("when_to_use", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skill_definition", "when_to_use")
