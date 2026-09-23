"""Execution Repository (WP-040).

Persistence for Execution domain. Enforces the spec §9 idempotency rule:
one Task has at most one ACTIVE (QUEUED/RUNNING) Execution at a time.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.execution.models import ACTIVE_STATUSES, Execution, ExecutionStatus


class ActiveExecutionExistsError(Exception):
    """Raised when creating an Execution for a Task that already has an active one."""


class ExecutionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self, project_id: int, task_id: int, correlation_id: str | None = None
    ) -> Execution:
        """Create a QUEUED Execution. Raises ActiveExecutionExistsError if one is active.

        Two-layer guard: fast path = read check; hard guarantee = partial unique
        index (AE-05 race-safe). IntegrityError from the index is translated.
        """
        from sqlalchemy.exc import IntegrityError

        active = await self.get_active_for_task(task_id)
        if active is not None:
            raise ActiveExecutionExistsError(
                f"Task {task_id} already has active Execution #{active.id} ({active.status})"
            )
        attempt = await self._next_attempt(task_id)
        ex = Execution(
            project_id=project_id,
            task_id=task_id,
            attempt_no=attempt,
            status=ExecutionStatus.QUEUED.value,
            phase="QUEUED",
            correlation_id=correlation_id,
        )
        self._session.add(ex)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise ActiveExecutionExistsError(
                f"Task {task_id} active Execution race lost at unique index"
            ) from e
        return ex

    async def get(self, execution_id: int) -> Execution | None:
        return await self._session.get(Execution, execution_id)

    async def get_active_for_task(self, task_id: int) -> Execution | None:
        stmt = (
            select(Execution)
            .where(
                Execution.task_id == task_id,
                Execution.status.in_([s.value for s in ACTIVE_STATUSES]),
            )
            .order_by(Execution.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def mark_running(self, ex: Execution, phase: str = "WORKER_EXECUTION") -> None:
        ex.status = ExecutionStatus.RUNNING.value
        ex.phase = phase
        ex.started_at = datetime.datetime.now(datetime.timezone.utc)
        await self._session.flush()

    async def mark_finished(
        self,
        ex: Execution,
        success: bool,
        message: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        skill_id: str | None = None,
        skill_version: str | None = None,
        skill_content_hash: str | None = None,
    ) -> None:
        ex.status = (
            ExecutionStatus.SUCCEEDED.value if success else ExecutionStatus.FAILED.value
        )
        ex.phase = "DONE" if success else "FAILED"
        ex.message = message
        ex.error_code = error_code
        ex.error_message = error_message
        ex.skill_id = skill_id
        ex.skill_version = skill_version
        ex.skill_content_hash = skill_content_hash
        ex.finished_at = datetime.datetime.now(datetime.timezone.utc)
        await self._session.flush()

    async def _next_attempt(self, task_id: int) -> int:
        stmt = (
            select(Execution.attempt_no)
            .where(Execution.task_id == task_id)
            .order_by(Execution.attempt_no.desc())
            .limit(1)
        )
        latest = (await self._session.execute(stmt)).scalar_one_or_none()
        return (latest or 0) + 1
