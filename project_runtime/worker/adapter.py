"""WorkerAdapter — abstract interface + MockWorker (V0.1).

Red-line rules (enforced by StateTransitionService, D03):
  1. Worker can only transition RUNNING → EXECUTION_DONE.
  2. Worker MUST NOT directly mark ACCEPTED.
  3. Worker can only start from READY status.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.task_graph.manager import TaskGraphManager

if TYPE_CHECKING:
    from project_runtime.artifact.manager import ArtifactManager
    from project_runtime.skills.loader import SkillExecutionContext

logger = logging.getLogger(__name__)


# ── data types ──────────────────────────────────────────────────────────────


@dataclass
class WorkPackage:
    task_id: int
    task_name: str
    playbook_ref: str | None = None
    contract: str | None = None
    # D17D: optional skill context (resolved by SkillResolver, loaded by SkillLoader)
    skill_context: "SkillExecutionContext | None" = None


@dataclass
class ExecutionResult:
    task_id: int
    success: bool
    output: str = ""
    artifacts: list[str] = field(default_factory=list)
    # D17D audit: actual skill used
    actual_skill_id: str | None = None
    actual_skill_version: str | None = None
    actual_skill_content_hash: str | None = None


# ── abstract interface ──────────────────────────────────────────────────────


class WorkerAdapter(ABC):
    """Unified Worker interface (WP-009)."""

    @abstractmethod
    async def assign(self, wp: WorkPackage) -> None:
        """Assign a WorkPackage to this worker."""

    @abstractmethod
    async def cancel(self, task_id: int) -> None:
        """Cancel a running task."""

    @abstractmethod
    async def health(self) -> str:
        """Return worker health status."""


# ── MockWorker (V0.1 — no external process) ─────────────────────────────────


class MockWorkerAdapter(WorkerAdapter):
    """Simulates worker execution without external process.

    On assign():
      1. Validates Task is READY.
      2. READY → RUNNING (via FSM).
      3. Simulates execution (async sleep).
      4. RUNNING → EXECUTION_DONE (via FSM).
      5. Creates TaskRun record (audit trail).
      6. Creates Artifact record via ArtifactManager (if provided).
    """

    def __init__(
        self,
        session: AsyncSession,
        simulate_delay: float = 0.01,
        artifact_mgr: "ArtifactManager | None" = None,
    ):
        self._session = session
        self._simulate_delay = simulate_delay
        self._fsm = StateTransitionService(session)
        self._mgr = TaskGraphManager(session)
        self._artifact_mgr = artifact_mgr
        self._running: dict[int, asyncio.Task] = {}
        # AsyncSession is NOT coroutine-safe: serialize all DB-touching
        # operations through this lock (batch-2 cancel race fix).
        self._db_lock = asyncio.Lock()

    async def assign(self, wp: WorkPackage) -> ExecutionResult:
        # Phase 1 (DB, locked): validate READY + RUNNING transition
        async with self._db_lock:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")

            # Red line 3: must be READY
            if task.status != TaskStatus.READY:
                raise ValueError(
                    f"Worker cannot start task in status {task.status.value}. Must be READY."
                )

            # READY → RUNNING (short tx: commit before simulated work — WP-041 §3)
            await self._fsm.transition_task(task, TaskStatus.RUNNING)
            await self._session.commit()

        # Simulate work (skill context, when provided, is consumed read-only)
        await asyncio.sleep(self._simulate_delay)
        result_output = f"Mock execution completed for task {wp.task_name}"
        if wp.skill_context is not None:
            sc = wp.skill_context
            result_output += f" [skill={sc.skill_id}@{sc.skill_version}]"

        # Phase 2 (short tx): re-fetch task (Phase-1 commit expired it)
        async with self._db_lock:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")

            # If cancelled during simulated work, do not force EXECUTION_DONE
            if task.status == TaskStatus.CANCELLED:
                return ExecutionResult(task_id=wp.task_id, success=False, output="cancelled")

            # RUNNING → EXECUTION_DONE (NEVER ACCEPTED — red line 1+2)
            await self._fsm.transition_task(task, TaskStatus.EXECUTION_DONE)
            await self._session.flush()

            # Create TaskRun audit record
            from project_runtime.project_state.models import TaskRun

            now = datetime.datetime.now(datetime.timezone.utc)
            run_payload: dict = {"output": result_output}
            if wp.skill_context is not None:
                run_payload["skill"] = {
                    "skill_id": wp.skill_context.skill_id,
                    "skill_version": wp.skill_context.skill_version,
                    "content_hash": wp.skill_context.skill_content_hash,
                }
            run = TaskRun(
                task_id=task.id,
                attempt=1,
                started_at=now - datetime.timedelta(seconds=self._simulate_delay),
                finished_at=now,
                result=json.dumps(run_payload),
            )
            self._session.add(run)

            # Create Artifact if manager is wired
            artifact_id: int | None = None
            if self._artifact_mgr is not None:
                artifact = await self._artifact_mgr.create_artifact(
                    project_id=task.project_id,
                    task_id=task.id,
                    logical_id=f"task-{task.id}-output",
                    name=f"{task.name}_output.txt",
                    content=result_output,
                )
                await self._session.flush()
                artifact_id = artifact.id

            await self._session.commit()  # short tx closes here (WP-041 §6)

        return ExecutionResult(
            task_id=wp.task_id,
            success=True,
            output=result_output,
            artifacts=[artifact_id] if artifact_id is not None else [],
            actual_skill_id=(
                wp.skill_context.skill_id if wp.skill_context else None
            ),
            actual_skill_version=(
                wp.skill_context.skill_version if wp.skill_context else None
            ),
            actual_skill_content_hash=(
                wp.skill_context.skill_content_hash if wp.skill_context else None
            ),
        )

    async def cancel(self, task_id: int) -> None:
        async with self._db_lock:
            task = await self._mgr.get_task(task_id)
            if task is None:
                return
            if task.status == TaskStatus.RUNNING:
                await self._fsm.transition_task(task, TaskStatus.CANCELLED)
                await self._session.flush()

    async def health(self) -> str:
        return "healthy"
