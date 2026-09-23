"""RH-01 add execution table (WP-040)

Revision ID: f8a1c9e2b3d4
Revises: 43fb23f1c555
Create Date: 2026-09-23

Execution domain table per 20-异步执行与Execution模型规范 §4.
Handwritten DDL (D17A lesson: never ship empty autogenerate shells).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a1c9e2b3d4"
down_revision: Union[str, Sequence[str], None] = "43fb23f1c555"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("task.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("phase", sa.String(64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("skill_id", sa.String(128), nullable=True),
        sa.Column("skill_version", sa.String(64), nullable=True),
        sa.Column("skill_content_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_execution_task_active", "execution", ["task_id", "status"])
    op.create_index("ix_execution_project", "execution", ["project_id"])
    # AE-05 hard guarantee: at most one ACTIVE (QUEUED/RUNNING) Execution per Task.
    # Partial unique index — read-then-write race loses at the DB level.
    op.execute(
        "CREATE UNIQUE INDEX ux_execution_task_active_one "
        "ON execution(task_id) WHERE status IN ('QUEUED','RUNNING')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_execution_task_active_one")
    op.drop_index("ix_execution_project", table_name="execution")
    op.drop_index("ix_execution_task_active", table_name="execution")
    op.drop_table("execution")
