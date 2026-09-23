"""Execution Service + Background Runner (WP-040 / ADR-041 / ADR-042).

Admission: POST creates QUEUED Execution, commits, returns 202 immediately.
Runner: background asyncio task picks up QUEUED Executions and runs them with
a DEDICATED session per phase — never the request session (DB-03), and never
holds a DB transaction across the LLM/network call (DB-04).

Task FSM mapping (spec §8):
  Execution RUNNING   → Task READY → RUNNING
  Execution SUCCEEDED → Task RUNNING → EXECUTION_DONE (never ACCEPTED — AE-07)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.execution.models import ExecutionStatus
from project_runtime.execution.repository import ExecutionRepository

logger = logging.getLogger(__name__)


class ExecutionRunner:
    """Background runner: executes one Execution end-to-end with short transactions."""

    async def run(self, execution_id: int) -> None:
        from project_runtime.artifact.manager import ArtifactManager
        from project_runtime.project_state.enums import TaskStatus
        from project_runtime.task_graph.manager import TaskGraphManager
        from project_runtime.worker.adapter import WorkPackage
        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        # Phase A: QUEUED → RUNNING (short tx). Task FSM transitions are owned
        # by the Worker (READY→RUNNING→EXECUTION_DONE, red lines intact).
        async with AsyncSession(engine) as session:
            repo = ExecutionRepository(session)
            ex = await repo.get(execution_id)
            if ex is None or ex.status != ExecutionStatus.QUEUED.value:
                return
            task_id, project_id = ex.task_id, ex.project_id
            await repo.mark_running(ex)

            mgr = TaskGraphManager(session)
            task = await mgr.get_task(task_id)
            if task is None:
                await repo.mark_finished(
                    ex, False, error_code="TASK_NOT_FOUND", error_message="task missing"
                )
                await session.commit()
                return
            # CREATED → READY before worker pickup (mirrors /plan-execute chain)
            if task.status == TaskStatus.CREATED:
                try:
                    await mgr.promote_to_ready(task_id)
                except Exception:
                    pass
                task = await mgr.get_task(task_id)
            task_name = task.name
            task_ready = task.status == TaskStatus.READY
            await session.commit()

        # Phase B: LLM execution — NO DB transaction held (DB-04)
        if not task_ready:
            async with AsyncSession(engine) as session:
                repo = ExecutionRepository(session)
                ex = await repo.get(execution_id)
                if ex is not None:
                    await repo.mark_finished(
                        ex, False, error_code="TASK_NOT_READY",
                        error_message="task not in READY state",
                    )
                    await session.commit()
            return

        worker_session = AsyncSession(engine)
        try:
            artifact_mgr = ArtifactManager(
                worker_session, workspace_root=f"data/tasks/{project_id}"
            )
            worker = LLMWorkerAdapter(worker_session, artifact_mgr=artifact_mgr)
            result = await worker.assign(WorkPackage(task_id=task_id, task_name=task_name))
            await worker_session.commit()
            success, message = result.success, result.output[:500]
            skill_id = result.actual_skill_id
            skill_version = result.actual_skill_version
            skill_hash = result.actual_skill_content_hash
            error = None if success else message
        except Exception as e:  # worker-level failure (e.g. not READY race)
            logger.error("Execution %s worker failed: %s", execution_id, e)
            await worker_session.rollback()
            success, message = False, None
            skill_id = skill_version = skill_hash = None
            error = str(e)[:500]
        finally:
            await worker_session.close()

        # Phase C: Execution → SUCCEEDED/FAILED (short tx). Task stays
        # EXECUTION_DONE — ACCEPTED belongs to Gate only (AE-07/AE-08).
        async with AsyncSession(engine) as session:
            repo = ExecutionRepository(session)
            ex = await repo.get(execution_id)
            if ex is None:
                return
            await repo.mark_finished(
                ex,
                success,
                message=message,
                error_code=None if success else "WORKER_FAILED",
                error_message=error,
                skill_id=skill_id,
                skill_version=skill_version,
                skill_content_hash=skill_hash,
            )
            await session.commit()


class ExecutionService:
    """Admission + query. No execution logic in the request lifecycle."""

    def __init__(self) -> None:
        self._runner = ExecutionRunner()

    async def submit(self, project_id: int, task_id: int) -> int:
        """Create QUEUED Execution (committed), spawn background runner, return id."""
        async with AsyncSession(engine) as session:
            repo = ExecutionRepository(session)
            ex = await repo.create(project_id, task_id)
            execution_id = ex.id
            await session.commit()
        # Background: runs beyond HTTP request lifecycle (ADR-041)
        asyncio.create_task(self._runner.run(execution_id))
        return execution_id
