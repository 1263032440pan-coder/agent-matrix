"""WorkerAdapter — abstract interface + MockWorker (V0.1).

Red-line rules (enforced by StateTransitionService, D03):
  1. Worker can only transition RUNNING → EXECUTION_DONE.
  2. Worker MUST NOT directly mark ACCEPTED.
  3. Worker can only start from READY status.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.task_graph.manager import TaskGraphManager

logger = logging.getLogger(__name__)


# ── data types ──────────────────────────────────────────────────────────────


@dataclass
class WorkPackage:
    task_id: int
    task_name: str
    playbook_ref: str | None = None
    contract: str | None = None


@dataclass
class ExecutionResult:
    task_id: int
    success: bool
    output: str = ""
    artifacts: list[str] = field(default_factory=list)


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
      5. NEVER transitions to ACCEPTED.
    """

    def __init__(self, session: AsyncSession, simulate_delay: float = 0.01):
        self._session = session
        self._simulate_delay = simulate_delay
        self._fsm = StateTransitionService(session)
        self._mgr = TaskGraphManager(session)
        self._running: dict[int, asyncio.Task] = {}

    async def assign(self, wp: WorkPackage) -> ExecutionResult:
        task = await self._mgr.get_task(wp.task_id)
        if task is None:
            raise ValueError(f"Task not found: {wp.task_id}")

        # Red line 3: must be READY
        if task.status != TaskStatus.READY:
            raise ValueError(
                f"Worker cannot start task in status {task.status.value}. Must be READY."
            )

        # READY → RUNNING
        await self._fsm.transition_task(task, TaskStatus.RUNNING)
        await self._session.flush()

        # Simulate work
        await asyncio.sleep(self._simulate_delay)

        # RUNNING → EXECUTION_DONE (NEVER ACCEPTED — red line 1+2)
        await self._fsm.transition_task(task, TaskStatus.EXECUTION_DONE)
        await self._session.flush()

        return ExecutionResult(
            task_id=wp.task_id,
            success=True,
            output=f"Mock execution completed for task {wp.task_name}",
        )

    async def cancel(self, task_id: int) -> None:
        task = await self._mgr.get_task(task_id)
        if task is None:
            return
        if task.status == TaskStatus.RUNNING:
            await self._fsm.transition_task(task, TaskStatus.CANCELLED)
            await self._session.flush()

    async def health(self) -> str:
        return "healthy"
