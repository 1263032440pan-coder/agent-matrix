"""Scheduler — dispatch READY tasks to Workers.

Rules (WP-008):
  - Only dispatches legal READY tasks.
  - Respects concurrency limit (max_workers).
  - No worker available → task stays READY (never lost).
  - Scheduler NEVER executes task internals — only calls WorkerAdapter.assign.
  - Automation Mode: FULL_AUTO dispatches directly; MANUAL creates ManualAction.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import (
    AutomationMode,
    ManualActionStatus,
    ManualActionType,
    TaskStatus,
)
from project_runtime.project_state.models import ManualAction, Project, Task
from project_runtime.worker.adapter import WorkerAdapter, WorkPackage

logger = logging.getLogger(__name__)


@dataclass
class DispatchDecision:
    task_id: int
    action: str  # "DISPATCHED" | "MANUAL_PENDING" | "SKIP_NO_CAPACITY" | "SKIP_NO_WORKER"
    detail: str = ""


class Scheduler:
    """V0.1 single-project scheduler."""

    def __init__(
        self,
        session: AsyncSession,
        worker: WorkerAdapter | None = None,
        max_workers: int = 1,
    ):
        self._session = session
        self._worker = worker
        self._max_workers = max_workers

    async def dispatch_once(self, project_id: int) -> list[DispatchDecision]:
        """One scheduling pass over a project's READY tasks."""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        decisions: list[DispatchDecision] = []

        # Current running count (concurrency control)
        running_count = await self._running_count(project_id)
        capacity = max(0, self._max_workers - running_count)

        # Ready candidates
        stmt = (
            select(Task)
            .where(Task.project_id == project_id, Task.status == TaskStatus.READY)
            .order_by(Task.id)
        )
        ready_tasks = list((await self._session.execute(stmt)).scalars().all())

        for task in ready_tasks:
            if capacity <= 0:
                decisions.append(
                    DispatchDecision(task.id, "SKIP_NO_CAPACITY", "max_workers reached")
                )
                continue

            if project.automation_mode == AutomationMode.MANUAL:
                # MANUAL: create ManualAction, do not dispatch
                action = ManualAction(
                    project_id=project_id,
                    task_id=task.id,
                    action_type=ManualActionType.TASK_DISPATCH,
                    proposal_payload=json.dumps(
                        {"task_id": task.id, "task_name": task.name}
                    ),
                    status=ManualActionStatus.PENDING,
                )
                self._session.add(action)
                decisions.append(
                    DispatchDecision(task.id, "MANUAL_PENDING", "ManualAction created")
                )
                continue

            # FULL_AUTO: dispatch to worker
            if self._worker is None:
                decisions.append(
                    DispatchDecision(task.id, "SKIP_NO_WORKER", "no worker registered")
                )
                continue

            await self._worker.assign(
                WorkPackage(task_id=task.id, task_name=task.name)
            )
            capacity -= 1
            decisions.append(DispatchDecision(task.id, "DISPATCHED"))

        await self._session.flush()
        return decisions

    async def confirm_manual_action(self, action_id: int) -> DispatchDecision:
        """Human confirms a ManualAction → actually dispatch the task."""
        action = await self._session.get(ManualAction, action_id)
        if action is None:
            raise ValueError(f"ManualAction not found: {action_id}")
        if action.status != ManualActionStatus.PENDING:
            raise ValueError(f"ManualAction {action_id} not PENDING")
        if self._worker is None:
            raise ValueError("No worker registered")

        action.status = ManualActionStatus.CONFIRMED
        task = await self._session.get(Task, action.task_id)
        await self._worker.assign(
            WorkPackage(task_id=task.id, task_name=task.name)
        )
        await self._session.flush()
        return DispatchDecision(task.id, "DISPATCHED", f"manual action {action_id}")

    async def _running_count(self, project_id: int) -> int:
        stmt = select(func.count(Task.id)).where(
            Task.project_id == project_id, Task.status == TaskStatus.RUNNING
        )
        return (await self._session.execute(stmt)).scalar_one()
