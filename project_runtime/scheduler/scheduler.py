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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import (
    AutomationMode,
    ManualActionStatus,
    ManualActionType,
    TaskStatus,
)
from project_runtime.project_state.models import ManualAction, Project, Task
from project_runtime.resource_governor.governor import (
    ResourceDecision,
    ResourceGovernor,
)
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
        use_llm_worker: bool = False,
    ):
        self._session = session
        if worker is not None:
            self._worker = worker
        elif use_llm_worker:
            from project_runtime.worker.llm_worker import LLMWorkerAdapter

            self._worker = LLMWorkerAdapter(session)
        else:
            self._worker = None
        self._governor = ResourceGovernor(session, max_workers=max_workers)

    async def dispatch_once(self, project_id: int) -> list[DispatchDecision]:
        """One scheduling pass over a project's READY tasks."""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")
        # Snapshot mode up-front: worker short-tx commits (WP-041) expire ORM
        # objects mid-loop; attribute access after commit would trigger sync IO.
        automation_mode = project.automation_mode

        decisions: list[DispatchDecision] = []

        # Ready candidates — snapshot ids (commit expires objects)
        stmt = (
            select(Task.id)
            .where(Task.project_id == project_id, Task.status == TaskStatus.READY)
            .order_by(Task.id)
        )
        ready_task_ids = list((await self._session.execute(stmt)).scalars().all())

        for task_id in ready_task_ids:
            # D15: dispatch gated by ResourceGovernor decision
            decision = await self._governor.evaluate(project_id)
            if decision == ResourceDecision.DEFER_RESOURCE:
                decisions.append(
                    DispatchDecision(
                        task_id, "SKIP_NO_CAPACITY", "DEFER_RESOURCE: max_workers reached"
                    )
                )
                continue

            if automation_mode == AutomationMode.MANUAL:
                # MANUAL: create ManualAction, do not dispatch.
                # Idempotent (D07-TD01 batch-1): skip if a PENDING action for
                # this task already exists.
                existing_action = await self._session.execute(
                    select(ManualAction).where(
                        ManualAction.task_id == task_id,
                        ManualAction.action_type == ManualActionType.TASK_DISPATCH,
                        ManualAction.status == ManualActionStatus.PENDING,
                    )
                )
                if existing_action.scalars().first() is not None:
                    decisions.append(
                        DispatchDecision(task_id, "MANUAL_PENDING", "already pending")
                    )
                    continue
                action = ManualAction(
                    project_id=project_id,
                    task_id=task_id,
                    action_type=ManualActionType.TASK_DISPATCH,
                    proposal_payload=json.dumps(
                        {"task_id": task_id, "task_name": f"task-{task_id}"}
                    ),
                    status=ManualActionStatus.PENDING,
                )
                self._session.add(action)
                decisions.append(
                    DispatchDecision(task_id, "MANUAL_PENDING", "ManualAction created")
                )
                continue

            # FULL_AUTO: dispatch to worker
            if self._worker is None:
                decisions.append(
                    DispatchDecision(task_id, "SKIP_NO_WORKER", "no worker registered")
                )
                continue

            # Fetch fresh task object (previous commit expired objects)
            task = await self._session.get(Task, task_id)
            if task is None:
                continue
            task_name = task.name  # read before worker's commit expires it
            await self._worker.assign(
                WorkPackage(task_id=task_id, task_name=task_name)
            )
            decisions.append(DispatchDecision(task_id, "DISPATCHED"))

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
        task_id = action.task_id
        task = await self._session.get(Task, task_id)
        task_name = task.name  # read before worker's commit expires objects
        await self._worker.assign(
            WorkPackage(task_id=task_id, task_name=task_name)
        )
        await self._session.flush()
        return DispatchDecision(task_id, "DISPATCHED", f"manual action {action_id}")
