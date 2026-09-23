"""RH-03 add runtime_event table (WP-042)

Revision ID: c3d7e8f4a9b1
Revises: f8a1c9e2b3d4
Create Date: 2026-09-23

Runtime Event Store per 22-Event-Engine与实时进度规范 §4/§9.
Handwritten DDL (D17A lesson).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7e8f4a9b1"
down_revision: Union[str, Sequence[str], None] = "f8a1c9e2b3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_event",
        # Integer not BigInteger — SQLite autoincrement needs INTEGER PRIMARY KEY
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("task.id"), nullable=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("execution.id"), nullable=True),
        sa.Column("phase", sa.String(64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(32), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("causation_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
    )
    op.create_index("ix_runtime_event_execution", "runtime_event", ["execution_id", "id"])
    op.create_index("ix_runtime_event_type", "runtime_event", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_runtime_event_type", table_name="runtime_event")
    op.drop_index("ix_runtime_event_execution", table_name="runtime_event")
    op.drop_table("runtime_event")
