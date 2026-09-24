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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.execution.models import ExecutionStatus
from project_runtime.execution.repository import ExecutionRepository

logger = logging.getLogger(__name__)


async def _emit(
    event_type, *, project_id=None, task_id=None, execution_id=None,
    phase=None, message=None, actor="RUNTIME", payload=None,
) -> None:
    """Best-effort event record (WP-042) — never breaks the execution path."""
    try:
        from project_runtime.event import EventEngine

        await EventEngine().record(
            event_type,
            project_id=project_id,
            task_id=task_id,
            execution_id=execution_id,
            phase=phase,
            message=message,
            actor=actor,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 — progress trail is best-effort
        logger.debug("event emit failed (%s)", event_type, exc_info=True)


class ExecutionRunner:
    """Background runner: executes one Execution end-to-end with short transactions."""

    async def run(self, execution_id: int) -> None:
        from project_runtime.artifact.manager import ArtifactManager
        from project_runtime.event import RuntimeEventType
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
                await _emit(
                    RuntimeEventType.EXECUTION_FAILED,
                    project_id=project_id, task_id=task_id, execution_id=execution_id,
                    phase="ADMISSION", message="task missing",
                    payload={"error_code": "TASK_NOT_FOUND"},
                )
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

        await _emit(
            RuntimeEventType.EXECUTION_STARTED,
            project_id=project_id, task_id=task_id, execution_id=execution_id,
            phase="RUNNING", message=f"execution {execution_id} started",
        )

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
            await _emit(
                RuntimeEventType.EXECUTION_FAILED,
                project_id=project_id, task_id=task_id, execution_id=execution_id,
                phase="ADMISSION", message="task not in READY state",
                payload={"error_code": "TASK_NOT_READY"},
            )
            return

        await _emit(
            RuntimeEventType.WORKER_STARTED,
            project_id=project_id, task_id=task_id, execution_id=execution_id,
            phase="WORKER_EXECUTION", message=f"worker executing: {task_name}",
        )

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
            artifact_ids = list(result.artifacts)
        except Exception as e:  # worker-level failure (e.g. not READY race)
            logger.error("Execution %s worker failed: %s", execution_id, e)
            await worker_session.rollback()
            success, message = False, None
            skill_id = skill_version = skill_hash = None
            error = str(e)[:500]
            artifact_ids = []
        finally:
            await worker_session.close()

        await _emit(
            RuntimeEventType.WORKER_COMPLETED,
            project_id=project_id, task_id=task_id, execution_id=execution_id,
            phase="WORKER_EXECUTION",
            message="worker completed" if success else "worker failed",
            payload={"success": success, "artifacts": artifact_ids},
        )
        for aid in artifact_ids:
            await _emit(
                RuntimeEventType.ARTIFACT_CREATED,
                project_id=project_id, task_id=task_id, execution_id=execution_id,
                phase="ARTIFACT", message=f"artifact #{aid}",
                payload={"artifact_id": aid},
            )

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

        await _emit(
            RuntimeEventType.EXECUTION_COMPLETED if success else RuntimeEventType.EXECUTION_FAILED,
            project_id=project_id, task_id=task_id, execution_id=execution_id,
            phase="DONE" if success else "FAILED",
            message=message if success else (error or "worker failed"),
            payload={"skill_id": skill_id, "skill_version": skill_version},
        )

        # Phase D (spec 04 执行链): Gate review after successful execution.
        # Worker stopped at EXECUTION_DONE (red line); ONLY Gate (actor=GATE)
        # may transition to ACCEPTED/REWORK. On ACCEPT (PASS), HARD-dependent
        # downstream tasks get unblocked by promote_to_ready on the next pass.
        if success:
            try:
                from project_runtime.project_state.models import Task as TaskModel
                from project_runtime.reviewer.gate import GateCore

                async with AsyncSession(engine) as session:
                    gate = GateCore(session)
                    result = await gate.evaluate(task_id)
                    gate_status = result.status.value
                    if gate_status == "PASS":
                        # Unblock HARD-dependent downstream tasks (ACCEPTED
                        # satisfies HARD deps) so the chain keeps flowing.
                        mgr = TaskGraphManager(session)
                        downstream = (
                            await session.execute(
                                select(TaskModel).where(
                                    TaskModel.project_id == project_id,
                                    TaskModel.status == TaskStatus.CREATED,
                                )
                            )
                        ).scalars().all()
                        for dt in downstream:
                            dt_id = dt.id
                            try:
                                await mgr.promote_to_ready(dt_id)
                            except Exception:
                                pass  # deps not yet satisfied — stays CREATED
                    await session.commit()
                logger.info(
                    "Execution %s gate result: %s", execution_id, gate_status
                )
            except Exception as e:  # noqa: BLE001 — gate failure must not
                # corrupt the execution record; task stays EXECUTION_DONE and
                # can be re-gated via ManualAction / future retry.
                logger.error("Execution %s gate evaluation failed: %s", execution_id, e)


class ExecutionService:
    """Admission + query. No execution logic in the request lifecycle."""

    def __init__(self) -> None:
        self._runner = ExecutionRunner()

    async def submit(self, project_id: int, task_id: int) -> int:
        """Create QUEUED Execution (committed), spawn background runner, return id."""
        from project_runtime.event import RuntimeEventType

        async with AsyncSession(engine) as session:
            repo = ExecutionRepository(session)
            ex = await repo.create(project_id, task_id)
            execution_id = ex.id
            await session.commit()
        await _emit(
            RuntimeEventType.EXECUTION_QUEUED,
            project_id=project_id, task_id=task_id, execution_id=execution_id,
            phase="QUEUED", message=f"execution {execution_id} queued",
        )
        # Background: runs beyond HTTP request lifecycle (ADR-041)
        asyncio.create_task(self._runner.run(execution_id))
        return execution_id
