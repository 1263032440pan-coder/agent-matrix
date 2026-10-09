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

# WP-034/WP-035-TD01: composition root — the hook executor is CONSTRUCTED
# once here and INJECTED into the PipelineEngine (no hidden singleton inside
# the executor/engine modules). Tests and future multi-registry setups
# construct their own pair explicitly.
from project_runtime.extension.registry import ExtensionRegistry  # noqa: E402
from project_runtime.extension.runtime import RuntimeHookExecutor  # noqa: E402
from project_runtime.pipeline.engine import get_shared_engine  # noqa: E402

hook_executor = RuntimeHookExecutor(ExtensionRegistry())
pipeline_engine = get_shared_engine(hook_executor)


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

    def _reschedule(self, execution_id: int, delay_s: float = 2.0) -> None:
        """WP-036: DEFER_RESOURCE → re-queue after a delay.

        The Execution stays QUEUED (never FAILED — core rule 3); a fresh
        runner attempt fires once the delay elapses. Pressure conditions
        are re-evaluated on every attempt.
        """
        import asyncio

        async def _retry() -> None:
            await asyncio.sleep(delay_s)
            try:
                await self.run(execution_id)
            except Exception:  # noqa: BLE001 — runner retries are best-effort
                logger.warning(
                    "deferred retry of execution %s failed", execution_id,
                    exc_info=True,
                )

        try:
            asyncio.get_running_loop().create_task(_retry())
        except RuntimeError:  # no running loop — nothing to schedule on
            logger.debug("no loop to reschedule execution %s", execution_id)

    async def run(self, execution_id: int) -> None:
        from project_runtime.artifact.manager import ArtifactManager
        from project_runtime.event import RuntimeEventType
        from project_runtime.project_state.enums import TaskStatus

        # WP-036: production wiring — governor with REAL psutil snapshot
        # (memory pressure genuinely active; 返修单观察1 方案a).
        from project_runtime.resource_governor.governor import (
            ResourceDecision,
            ResourceGovernor,
        )
        from project_runtime.resource_governor.profiles import snapshot
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

            # WP-036: admission gate — memory pressure / slot exhaustion →
            # DEFER (task stays READY, execution re-queued; NEVER FAIL).
            gov = ResourceGovernor(
                session, profile=None, snapshot_provider=snapshot
            )
            decision = await gov.evaluate(project_id)
            if decision is ResourceDecision.DEFER_RESOURCE:
                await session.rollback()
                self._reschedule(execution_id, delay_s=2.0)
                return
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

        # WP-034: fire worker.before_execute — extensions may PAUSE/RETRY/
        # NEED_USER_INPUT (advisory: Core decides; PROPOSAL goes through
        # Core Validation → Transaction, never direct mutation).
        hook_payload = {
            "execution_id": execution_id, "task_id": task_id,
            "task_name": task_name, "project_id": project_id,
        }
        try:
            outcome = await hook_executor.fire_hook(
                "worker.before_execute", hook_payload
            )
            if outcome.control.value != "CONTINUE":
                logger.info(
                    "worker.before_execute control=%s (extensions advise; "
                    "Core decides)", outcome.control.value,
                )
                await _emit(
                    RuntimeEventType.WORKER_COMPLETED,
                    project_id=project_id, task_id=task_id,
                    execution_id=execution_id,
                    phase="WORKER_EXECUTION",
                    message=f"hook advised {outcome.control.value}",
                    payload={"hook": "worker.before_execute",
                             "results": outcome.results[:5]},
                )
                return  # advisory pause for V0.1: do not dispatch this task now
        except Exception:  # noqa: BLE001 — hook plane must never break execution
            logger.debug("before_execute hook failed", exc_info=True)

        # Phase B-0 (D17C/D17D + 用户需求: 按任务内容自动选 skill):
        # 1) 项目显式绑定 → SkillResolver（用户意志优先）
        # 2) 无绑定 → SkillSelector 按任务内容 LLM 自动选
        #    （name + description + when_to_use 作为 tool 列表）
        # resolution failure never blocks execution (runs without a skill).
        skill_ctx = None
        try:
            from project_runtime.skills import (
                SkillLoader,
                SkillRequirement,
                SkillResolver,
                SkillType,
            )
            from project_runtime.skills.selector import SkillSelector

            async with AsyncSession(engine) as s:
                resolver = SkillResolver(s)
                resolution = await resolver.resolve(
                    SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=False),
                    project_id=project_id,
                )
                resolved_def = resolution.resolved_skill
                select_reason = (
                    resolution.reason.value if resolution.reason else "bound"
                )
                if (
                    resolution.status.value != "RESOLVED"
                    or resolved_def is None
                ):
                    # 无绑定命中 → LLM 按任务内容自动选（TASK_EXECUTION
                    # 优先，无则不限类型——方法类 skill 的具体阶段指引
                    # 同样能指导单个任务的执行）
                    task_content = _task_select_content(task)
                    resolved_def = await SkillSelector(s).select(
                        task_content=task_content,
                        extra_context=(
                            f"项目任务执行：{task_name}"
                        ),
                    )
                    select_reason = "LLM_AUTO_SELECT"
                if resolved_def is not None:
                    loader = SkillLoader(s)
                    skill_ctx = await loader.load(
                        resolved_def.skill_id,
                        resolved_def.version,
                        expected_hash=resolved_def.content_hash,
                    )
                    logger.info(
                        "Execution %s skill: %s@%s (%s)",
                        execution_id,
                        skill_ctx.skill_id,
                        skill_ctx.skill_version,
                        select_reason,
                    )
                    await _emit(
                        RuntimeEventType.SKILL_RESOLVED,
                        project_id=project_id, task_id=task_id, execution_id=execution_id,
                        phase="SKILL",
                        message=(
                            f"skill {skill_ctx.skill_id}@{skill_ctx.skill_version}"
                            f" ({select_reason})"
                        ),
                    )
        except Exception as e:  # noqa: BLE001 — skill is optional guidance
            logger.debug("skill resolution failed for %s: %s", execution_id, e)

        # 偏离③ 兑现: 执行前清理 pending 研究（联网搜索 + LLM 综合
        # → ANSWERED——下游 Playbook/replanner 可消费）
        try:
            from project_runtime.research.executor import WebResearchExecutor

            async with AsyncSession(engine) as s:
                ex = WebResearchExecutor(s)
                answered = await ex.execute_pending(limit=3)
            if answered:
                logger.info(
                    "pre-execution research answered: %s", answered
                )
        except Exception:  # noqa: BLE001 — 研究绝不阻断执行
            logger.debug("pre-execution research sweep failed", exc_info=True)

        # WP-010: build the minimal-sufficient context package and bind it
        # into the WorkPackage contract slot (planning → execution bridge).
        context_json = None
        try:
            from project_runtime.context import ContextBuilder

            async with AsyncSession(engine) as s:
                builder = ContextBuilder(s, budget_tokens=8000)
                pkg = await builder.build(project_id, task_id)
                context_json = pkg.to_json()
        except Exception:  # noqa: BLE001 — context is best-effort enrichment
            logger.debug("context build failed for %s", execution_id,
                         exc_info=True)

        worker_session = AsyncSession(engine)
        try:
            artifact_mgr = ArtifactManager(
                worker_session, workspace_root=f"data/tasks/{project_id}"
            )
            worker = LLMWorkerAdapter(worker_session, artifact_mgr=artifact_mgr)
            result = await worker.assign(
                WorkPackage(
                    task_id=task_id,
                    task_name=task_name,
                    skill_context=skill_ctx,
                    # WP-031 binding trace
                    contract=context_json,
                    skill_requirement=(
                        "OPTIONAL" if skill_ctx is not None else "NONE"
                    ),
                    resolved_skill_ref=(
                        f"{skill_ctx.skill_id}@{skill_ctx.skill_version}"
                        if skill_ctx is not None else None
                    ),
                    skill_execution_context_ref=(
                        f"skill-ctx:{skill_ctx.skill_id}:{skill_ctx.skill_content_hash[:12]}"
                        if skill_ctx is not None else None
                    ),
                )
            )
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
            # Safety net: the worker may have crashed BETWEEN the RUNNING
            # transition and its own FAILED handling (e.g. artifact write
            # error) — leave no task stranded in RUNNING forever.
            try:
                from project_runtime.project_state.enums import TaskStatus
                from project_runtime.task_graph.manager import TaskGraphManager

                async with AsyncSession(engine) as s2:
                    mgr2 = TaskGraphManager(s2)
                    t = await mgr2.get_task(task_id)
                    if t is not None and t.status == TaskStatus.RUNNING:
                        from project_runtime.project_state.fsm import (
                            StateTransitionService,
                        )

                        await StateTransitionService(s2).transition_task(
                            t, TaskStatus.FAILED
                        )
                        await s2.commit()
                        logger.info(
                            "Execution %s: stranded RUNNING task %d → FAILED",
                            execution_id, task_id,
                        )
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.debug("stranded-task cleanup failed", exc_info=True)
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

        # WP-034: fire worker.after_execute (observers/policy — advisory)
        try:
            await hook_executor.notify(
                "worker.after_execute",
                {"execution_id": execution_id, "task_id": task_id,
                 "project_id": project_id, "success": success},
            )
        except Exception:  # noqa: BLE001
            logger.debug("after_execute hook failed", exc_info=True)

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

                # FULL_AUTO chain progression: when Gate PASS unblocks
                # downstream tasks, submit them automatically — the project
                # runs end-to-end without per-step clicking. MANUAL mode is
                # untouched (no auto submit — its red line).
                if gate_status == "PASS":
                    await self._auto_advance(project_id)
            except Exception as e:  # noqa: BLE001 — gate failure must not
                # corrupt the execution record; task stays EXECUTION_DONE and
                # can be re-gated via ManualAction / future retry.
                logger.error("Execution %s gate evaluation failed: %s", execution_id, e)

    async def _auto_advance(self, project_id: int) -> None:
        """FULL_AUTO: submit executions for all READY tasks (best-effort).

        Called after every Gate PASS. Idempotent: the active-execution guard
        (partial unique index, AE-05) rejects duplicates, so overlapping
        calls are safe. MANUAL projects are skipped.
        """
        from project_runtime.execution.repository import (
            ActiveExecutionExistsError,
        )
        from project_runtime.project_state.enums import (
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            Project as ProjectModel,
        )
        from project_runtime.project_state.models import (
            Task as TaskModel,
        )

        try:
            async with AsyncSession(engine) as session:
                proj = await session.get(ProjectModel, project_id)
                if proj is None or proj.automation_mode != AutomationMode.FULL_AUTO:
                    return  # MANUAL: never auto-submit
                ready_ids = list(
                    (
                        await session.execute(
                            select(TaskModel).where(
                                TaskModel.project_id == project_id,
                                TaskModel.status == TaskStatus.READY,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                ids = [t.id for t in ready_ids]

            # Throttle: at most MAX_AUTO_SUBMIT per advance — wide graphs
            # (many independent READY tasks) would flood the LLM gateway.
            # Sequential chains naturally submit 1 at a time.
            max_submit = 3
            submitted = 0
            for tid in ids[:max_submit]:
                try:
                    await ExecutionService().submit(project_id, tid)
                    submitted += 1
                except ActiveExecutionExistsError:
                    pass  # already running — fine
            if submitted:
                logger.info(
                    "FULL_AUTO advance: submitted %d execution(s) for project %d",
                    submitted, project_id,
                )
        except Exception:  # noqa: BLE001 — auto-advance is best-effort
            logger.debug("auto-advance failed", exc_info=True)


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


def _task_select_content(task) -> str:
    """选 skill 用的任务内容（任务名 + 描述 + 合同文本）。"""
    parts = [task.name or ""]
    desc = getattr(task, "description", None)
    if desc:
        parts.append(str(desc))
    contract = getattr(task, "contract", None)
    if contract:
        parts.append(str(contract))
    return "\n".join(p for p in parts if p.strip())
