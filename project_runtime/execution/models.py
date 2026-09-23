"""Execution domain model (WP-040 / ADR-041 / 20-异步执行与Execution模型规范).

Task != Execution: an Execution is one concrete execution attempt against a Task.
Task keeps its V1.11 FSM; Execution has its own minimal lifecycle:

    QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED

Red lines (spec §10):
  - Worker never sets Task ACCEPTED via Execution.
  - Retries create a NEW Execution (attempt_no+1), never overwrite.
  - One Task has at most one ACTIVE (QUEUED/RUNNING) Execution.
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class ExecutionStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATUSES = (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)


class Execution(Base):
    __tablename__ = "execution"
    # AE-05 hard guarantee: at most one ACTIVE Execution per Task (DB-level,
    # survives read-then-write races). Mirrors migration f8a1c9e2b3d4.
    __table_args__ = (
        Index(
            "ux_execution_task_active_one",
            "task_id",
            unique=True,
            sqlite_where=text("status IN ('QUEUED','RUNNING')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[ExecutionStatus] = mapped_column(
        String(16), nullable=False, default=ExecutionStatus.QUEUED.value
    )
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
