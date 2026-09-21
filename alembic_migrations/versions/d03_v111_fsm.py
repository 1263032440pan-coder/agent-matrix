"""V1.11 FSM enums — update ProjectStatus and TaskStatus value domains.

Revision ID: d03_v111_fsm
Revises: fab9f2c0ba3d
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d03_v111_fsm"
down_revision: Union[str, Sequence[str], None] = "fab9f2c0ba3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# V1.10 → V1.11 data migration map (SQLite only — no-op on empty dev DBs)
_TASK_STATUS_MAP = {
    "PENDING": "CREATED",
    "DISPATCHED": "RUNNING",
}
_PROJECT_STATUS_MAP = {
    "UNDERSTANDING": "INITIALIZING",
    "PLANNING": "INITIALIZING",
    "EXECUTING": "ACTIVE",
    "FAILED": "CANCELLED",
}


def _migrate_task_status(connection, table_name: str):
    """Rewrite old task status values to V1.11 equivalents."""
    for old_val, new_val in _TASK_STATUS_MAP.items():
        connection.execute(
            sa.text(
                f"UPDATE {table_name} SET status = :new WHERE status = :old"
            ),
            {"new": new_val, "old": old_val},
        )


def _migrate_project_status(connection, table_name: str):
    for old_val, new_val in _PROJECT_STATUS_MAP.items():
        connection.execute(
            sa.text(
                f"UPDATE {table_name} SET status = :new WHERE status = :old"
            ),
            {"new": new_val, "old": old_val},
        )


def upgrade() -> None:
    # ── Task table: replace taskstatus enum ─────────────────────────────
    with op.batch_alter_table("task", recreate="always") as batch:
        _migrate_task_status(op.get_bind(), "task")
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "PENDING",
                "READY",
                "DISPATCHED",
                "RUNNING",
                "EXECUTION_DONE",
                "ACCEPTED",
                "FAILED",
                "CANCELLED",
                name="taskstatus",
            ),
            type_=sa.Enum(
                "CREATED",
                "READY",
                "RUNNING",
                "EXECUTION_DONE",
                "REWORK",
                "BLOCKED",
                "FAILED",
                "ACCEPTED",
                "CANCELLED",
                name="taskstatus",
            ),
        )

    # ── TaskRun table: same enum change ────────────────────────────────
    with op.batch_alter_table("task_run", recreate="always") as batch:
        _migrate_task_status(op.get_bind(), "task_run")
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "PENDING",
                "READY",
                "DISPATCHED",
                "RUNNING",
                "EXECUTION_DONE",
                "ACCEPTED",
                "FAILED",
                "CANCELLED",
                name="taskstatus",
            ),
            type_=sa.Enum(
                "CREATED",
                "READY",
                "RUNNING",
                "EXECUTION_DONE",
                "REWORK",
                "BLOCKED",
                "FAILED",
                "ACCEPTED",
                "CANCELLED",
                name="taskstatus",
            ),
        )

    # ── Project table: replace projectstatus enum ──────────────────────
    with op.batch_alter_table("project", recreate="always") as batch:
        _migrate_project_status(op.get_bind(), "project")
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "CREATED",
                "UNDERSTANDING",
                "PLANNING",
                "EXECUTING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="projectstatus",
            ),
            type_=sa.Enum(
                "CREATED",
                "INITIALIZING",
                "ACTIVE",
                "BLOCKED",
                "COMPLETING",
                "COMPLETED",
                "CANCELLED",
                name="projectstatus",
            ),
        )


def downgrade() -> None:
    # Revert to V1.10 enums (lossy — V1.11 values map back to nearest V1.10)
    _TASK_REVERT_MAP = {
        "CREATED": "PENDING",
        "REWORK": "EXECUTION_DONE",
        "BLOCKED": "PENDING",
    }
    _PROJECT_REVERT_MAP = {
        "INITIALIZING": "UNDERSTANDING",
        "ACTIVE": "EXECUTING",
        "BLOCKED": "EXECUTING",
        "COMPLETING": "EXECUTING",
    }

    with op.batch_alter_table("task", recreate="always") as batch:
        for new_val, old_val in _TASK_REVERT_MAP.items():
            op.get_bind().execute(
                sa.text("UPDATE task SET status = :old WHERE status = :new"),
                {"old": old_val, "new": new_val},
            )
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "CREATED", "READY", "RUNNING", "EXECUTION_DONE",
                "REWORK", "BLOCKED", "FAILED", "ACCEPTED", "CANCELLED",
                name="taskstatus",
            ),
            type_=sa.Enum(
                "PENDING", "READY", "DISPATCHED", "RUNNING",
                "EXECUTION_DONE", "ACCEPTED", "FAILED", "CANCELLED",
                name="taskstatus",
            ),
        )

    with op.batch_alter_table("task_run", recreate="always") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "CREATED", "READY", "RUNNING", "EXECUTION_DONE",
                "REWORK", "BLOCKED", "FAILED", "ACCEPTED", "CANCELLED",
                name="taskstatus",
            ),
            type_=sa.Enum(
                "PENDING", "READY", "DISPATCHED", "RUNNING",
                "EXECUTION_DONE", "ACCEPTED", "FAILED", "CANCELLED",
                name="taskstatus",
            ),
        )

    with op.batch_alter_table("project", recreate="always") as batch:
        for new_val, old_val in _PROJECT_REVERT_MAP.items():
            op.get_bind().execute(
                sa.text("UPDATE project SET status = :old WHERE status = :new"),
                {"old": old_val, "new": new_val},
            )
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "CREATED", "INITIALIZING", "ACTIVE", "BLOCKED",
                "COMPLETING", "COMPLETED", "CANCELLED",
                name="projectstatus",
            ),
            type_=sa.Enum(
                "CREATED", "UNDERSTANDING", "PLANNING", "EXECUTING",
                "COMPLETED", "FAILED", "CANCELLED",
                name="projectstatus",
            ),
        )