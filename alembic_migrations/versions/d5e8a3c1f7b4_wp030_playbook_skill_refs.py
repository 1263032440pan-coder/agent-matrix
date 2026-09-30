"""WP-030 playbook skill refs columns

Revision ID: d5e8a3c1f7b4
Revises: c4d9f2a8e6b1
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8a3c1f7b4"
down_revision: Union[str, Sequence[str], None] = "c4d9f2a8e6b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_playbook",
                  sa.Column("project_method_skill_refs", sa.Text(), nullable=True))
    op.add_column("project_playbook",
                  sa.Column("task_skill_preferences", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_playbook", "task_skill_preferences")
    op.drop_column("project_playbook", "project_method_skill_refs")
