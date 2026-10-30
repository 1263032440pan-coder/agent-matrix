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

PLAN_STATUS_READY = "READY"
PLAN_STATUS_ASSIGNING = "ASSIGNING"
PLAN_STATUS_INVALID = "INVALID"


class PlanNotReadyError(Exception):
    """plan_status ≠ READY 时拒绝 submit / FULL_AUTO / 手动执行。"""

    def __init__(self, plan_status: str | None):
        self.plan_status = plan_status or "NULL"
        self.error_code = "PLAN_NOT_READY"
        super().__init__(
            f"计划未就绪（plan_status={self.plan_status}），拒绝执行"
        )


async def assert_plan_ready_for_execution(
    session: AsyncSession, project_id: int,
) -> None:
    """SYS-106：仅 READY（或遗留 NULL）允许进入执行通道。"""
    from project_runtime.project_state.models import Project as ProjectModel

    proj = await session.get(ProjectModel, project_id)
    if proj is None:
        raise PlanNotReadyError("MISSING")
    status = getattr(proj, "plan_status", None)
    if status is None or status == PLAN_STATUS_READY:
        return
    raise PlanNotReadyError(status)


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
            # SYS-146: HARD 上游未齐则不启动 Worker（任务保持 READY，不 FAILED）
            if task_ready:
                try:
                    deps_ok = await mgr.can_set_ready(task_id)
                except Exception:  # noqa: BLE001
                    deps_ok = True
                if not deps_ok:
                    await session.rollback()
                    self._reschedule(execution_id, delay_s=5.0)
                    return
            await repo.mark_running(ex)
            # 快照（commit 后 ORM 过期——MissingGreenlet/Detached 老坑）:
            # Phase B-0 选 skill 需要的任务内容 + Agent 绑定需要的 task_type
            task_select_content = _task_select_content(task)
            task_type_snapshot = task.task_type
            task_contract_snapshot = task.contract
            task_playbook_ref_snapshot = task.playbook_ref
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
        # 20号规范批次3 FSM 接线: 派发前绑定 Agent 实例（BUSY +
        # current_task_id; best-effort——无实例照常执行老路径）
        agent_binding = None
        try:
            from project_runtime.worker.lifecycle import bind_agent

            async with AsyncSession(engine) as s:
                agent_binding = await bind_agent(
                    s, project_id, task_id,
                    task_type=task_type_snapshot,
                    task_contract=task_contract_snapshot,
                    task_content=task_select_content or "",
                )
                await s.commit()
        except Exception:  # noqa: BLE001 — Agent 面绝不阻断执行
            logger.debug("agent bind failed", exc_info=True)
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
                task_content = task_select_content
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
                    # V1.4: Agent skill 白名单过滤（None=全局候选池）
                    selector = SkillSelector(s)
                    if agent_binding is not None and (
                        agent_binding.skill_whitelist is not None
                    ):
                        selector = selector.restrict_to(
                            agent_binding.skill_whitelist
                        )
                    resolved_def = await selector.select(
                        task_content=task_content,
                        extra_context=(
                            f"项目任务执行：{task_name}"
                        ),
                    )
                    select_reason = "LLM_AUTO_SELECT"
                # 用户决策（2026-10-09）: 联网 RAG 只用于获取必要信息完成
                # 当前任务（WebResearchExecutor 研究链），不生成 skill——
                # skill 必须用户自己安装。无可用 skill → 裸跑（研究清扫
                # 产出的 ANSWERED ResearchRecord 经 ContextBuilder 进上下文）。
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
                    playbook_ref=task_playbook_ref_snapshot,
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
                    # V1.4: Agent 白名单透传（None=不限 / list=交集）
                    tool_whitelist=(
                        agent_binding.tool_whitelist if agent_binding else None
                    ),
                    skill_whitelist=(
                        agent_binding.skill_whitelist if agent_binding else None
                    ),
                    # V1.5a-1: code_stage 透传（playbook_ref 解析——软提醒用）
                    code_stage=_code_stage_of(task_playbook_ref_snapshot),
                    # V1.6 序0: 任务工作区根（file_rw 写禁闭）
                    workspace_root=f"data/tasks/{project_id}/{task_id}",
                    # V1.6 序3: delivery_tags 透传（收工自查钩子消费）
                    delivery_tags=_delivery_tags_of(
                        task_playbook_ref_snapshot),
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

        # 20号规范批次3 FSM 接线: Agent 实例生命周期回收
        # （成功→release BUSY→IDLE+记账; 失败→record_failure 3 连败
        #   DEGRADED+强制快照+传播三段链; best-effort 绝不阻断）
        if agent_binding is not None:
            try:
                from project_runtime.worker.lifecycle import (
                    fail_agent,
                    release_agent,
                )

                async with AsyncSession(engine) as s:
                    if success:
                        await release_agent(s, agent_binding)
                    else:
                        await fail_agent(
                            s, agent_binding,
                            reason=f"execution {execution_id} failed",
                        )
                    await s.commit()
            except Exception:  # noqa: BLE001 — Agent 面绝不阻断执行
                logger.debug("agent release/fail failed", exc_info=True)
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

        # 返修单 A 层③b: 断流类失败自动重试（60s 后重提交, 上限 2 次
        # ——attempt 计数在 task_run; 超限如实 FAILED 留 UI 手动通道）
        if not success and error:
            _stream_err = any(
                k in (error or "")
                for k in ("APIConnection", "MidStream", "Timeout")
            )
            if _stream_err:
                from sqlalchemy import select as _select

                from project_runtime.project_state.models import (
                    TaskRun as _TaskRun,
                )

                try:
                    async with AsyncSession(engine) as _s:
                        _n = (
                            await _s.execute(
                                _select(_TaskRun.id).where(
                                    _TaskRun.task_id == task_id
                                )
                            )
                        )
                        _attempts = len(_n.scalars().all())
                    if _attempts <= 3:  # 原始 + 自动重试 2 次
                        import asyncio as _aio

                        async def _auto_retry():
                            await _aio.sleep(60)
                            try:
                                await ExecutionService().submit(
                                    project_id, task_id
                                )
                                logger.info(
                                    "auto-retry submitted for task %d "
                                    "(stream failure, attempt %d)",
                                    task_id, _attempts,
                                )
                            except Exception:  # noqa: BLE001
                                logger.debug(
                                    "auto-retry failed for task %d",
                                    task_id, exc_info=True,
                                )

                        _aio.get_event_loop().create_task(_auto_retry())
                except Exception:  # noqa: BLE001 — 自动重试是增强
                    logger.debug("auto-retry check failed", exc_info=True)

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
                    if gate_status == "FAIL":
                        # 返修单-闭环接线 P0-1 修正: FAIL 落 REWORK 后同样
                        # 触发 advance——REWORK 自动接线（60s 冷却+轮次闸）
                        # 在 _auto_advance 里, FAIL 路径不经过则成死代码
                        # （实测: 补建任务 FAIL 躺平 4 分钟无人拉起暴露）
                        try:
                            await self._auto_advance(project_id)
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "post-FAIL advance failed", exc_info=True,
                            )
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
                    # 20号规范批次2: 五闸④全局熔断检查（连续 8 FAIL/
                    # REWORK → 项目 PAUSED 停止推进）+ Controller 清单
                    # 对账（all_green 才允许继续 advance——停止条件=
                    # 清单全绿而非队列空，§3.4 驱动层）
                    try:
                        from project_runtime.worker.controller import (
                            Controller,
                        )

                        async with AsyncSession(engine) as csession:
                            ctl = Controller(csession)
                            fused = await ctl.check_global_fuse(project_id)
                            report = None
                            if not fused:
                                report = await ctl.reconcile(project_id)
                            await csession.commit()
                        if fused:
                            logger.warning(
                                "Execution %s: global fuse tripped for "
                                "project %d — auto-advance suppressed",
                                execution_id, project_id,
                            )
                            return
                        if report is not None and not report.all_green \
                                and report.stuck:
                            # 有卡点: Controller 已发 correction（对账
                            # act 内联在 reconcile 事件里；批次 3 接
                            # 补任务）——仍推进 READY 下游（不因单点
                            # 卡死整链）
                            logger.info(
                                "Execution %s: reconciliation found stuck "
                                "tasks %s — corrections dispatched",
                                execution_id, report.stuck[:5],
                            )
                    except Exception:  # noqa: BLE001 — Controller 巡检绝不阻断执行
                        logger.debug(
                            "controller reconcile failed", exc_info=True
                        )
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
        from sqlalchemy import select as _sel_count

        from project_runtime.execution.repository import (
            ActiveExecutionExistsError,
        )
        from project_runtime.project_state.enums import (
            AutomationMode,
            ProjectStatus,
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
                # 20号规范批次2 五闸④: 熔断态（PAUSED）项目不推进
                if proj.status == ProjectStatus.PAUSED:
                    return  # global fuse — no submissions
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
                from project_runtime.task_graph.manager import (
                    TaskGraphManager as _TgmAdv,
                )

                _mgr_adv = _TgmAdv(session)
                ids = []
                for t in ready_ids:
                    try:
                        if await _mgr_adv.can_set_ready(t.id):
                            ids.append(t.id)
                    except Exception:  # noqa: BLE001
                        ids.append(t.id)
                # 返修单-闭环接线 P0-1: REWORK 任务自动接线——
                # FSM 合法转移 REWORK→READY（fsm.py:29 此前无调用方,
                # 强制门打回后躺平靠手动拉）。轮次闸: gate FAIL >= 3 不拉
                # （ManualAction 熔断已兜底）; 冷却: 打回 60s 内不抖动重跑
                import datetime as _dt
                import time as _time

                from project_runtime.project_state.models import (
                    GateResult as _GateRes,
                )

                rework_ids = list(
                    (
                        await session.execute(
                            select(TaskModel).where(
                                TaskModel.project_id == project_id,
                                TaskModel.status == TaskStatus.REWORK,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                _now = _time.time()
                for _rt in rework_ids:
                    from sqlalchemy import func as _f2

                    _fails = (
                        await session.execute(
                            _sel_count(_f2.count()).where(
                                _GateRes.task_id == _rt.id,
                                _GateRes.status == 'FAIL',
                            )
                        )
                    ).scalar() or 0
                    if _fails >= 3:
                        continue  # 熔断已发生——留给 ManualAction
                    # 时区修正: DB 的 updated_at 存 UTC naive——
                    # timestamp() 按本地时区解析会把 UTC 当本地, _age
                    # 为负（未来 8h）→ 冷却永远跳过（实测任务8 躺平根因）
                    _updated = _rt.updated_at
                    try:
                        _age = _now - _updated.replace(
                            tzinfo=_dt.timezone.utc
                        ).timestamp()
                    except (AttributeError, OSError):
                        _age = 999
                    if _age < 60:
                        continue  # 冷却未过
                    # SYS-146: 心跳也须 HARD 上游已 ACCEPTED
                    try:
                        if not await _mgr_adv.can_set_ready(_rt.id):
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                    from project_runtime.project_state.fsm import (
                        StateTransitionService as _Sts,
                    )

                    await _Sts(session).transition_task(
                        _rt, TaskStatus.READY
                    )
                    await session.commit()
                    ids.append(_rt.id)

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

            # V1.6 序5 + 返修单v2 P1: 项目收口对账——每次 advance 尾部
            # 无条件触发（不只无 READY 时——补建任务 ACCEPTED 后的最后一跳
            # 曾因 ids 快照时序漏检）。幂等安全: 二次收口若还缺件,
            # '同名任务存在不再建' 直接短路, 只发 RECONCILIATION_GAP
            # 事件 + UI 标红, 不再造任务（防死循环——v2 §一接线）。
            try:
                await _delivery_closeout_check(project_id)
            except Exception:  # noqa: BLE001 — 收口 best-effort
                logger.debug("delivery closeout failed", exc_info=True)
        except Exception:  # noqa: BLE001 — auto-advance is best-effort
            logger.debug("auto-advance failed", exc_info=True)


async def _delivery_closeout_check(project_id: int) -> None:
    """V1.6d: 收口——优先冻结脚本扫项目根；脚本无效则事件不瞎补。"""
    import json as _json
    from pathlib import Path

    from project_runtime.event import EventEngine, RuntimeEventType
    from project_runtime.procedure.delivery_manifest import (
        check_match,
        validate_manifest,
        workspace_file_names,
    )
    from project_runtime.procedure.delivery_validator import (
        run_validator_script,
        sha256_file,
        validator_path,
    )
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.project_state.models import Artifact
    from project_runtime.project_state.models import (
        Project as ProjectModel,
    )
    from project_runtime.project_state.models import (
        Task as TaskModel,
    )
    from project_runtime.task_graph.manager import (
        CreateTaskRequest,
        TaskGraphManager,
    )

    async with AsyncSession(engine) as session:
        proj = await session.get(ProjectModel, project_id)
        if proj is None:
            return
        active = list(
            (
                await session.execute(
                    select(TaskModel).where(
                        TaskModel.project_id == project_id,
                        TaskModel.status.in_([
                            TaskStatus.CREATED, TaskStatus.READY,
                            TaskStatus.RUNNING, TaskStatus.EXECUTION_DONE,
                            TaskStatus.REWORK,
                        ]),
                    )
                )
            ).scalars().all()
        )
        if active:
            return

        arts = list(
            (
                await session.execute(
                    select(Artifact).where(Artifact.project_id == project_id)
                )
            ).scalars().all()
        )
        all_names = [a.name or "" for a in arts]
        ws_root = Path(f"data/tasks/{project_id}")
        if ws_root.exists():
            all_names = list({*all_names, *workspace_file_names(ws_root)})

        missing_items: list[dict] = []
        manifest = None
        if proj.delivery_manifest:
            try:
                manifest = validate_manifest(proj.delivery_manifest)
            except Exception:  # noqa: BLE001
                logger.debug("closeout manifest invalid", exc_info=True)
                manifest = None

        script = validator_path(project_id)
        expected = proj.delivery_validator_hash
        actual = sha256_file(script) if script.exists() else None
        script_ok = bool(expected and actual == expected)

        if manifest and expected:
            if not script_ok:
                await EventEngine().record(
                    RuntimeEventType.DELIVERY_VALIDATOR_TAMPERED,
                    project_id=project_id,
                    phase="CLOSEOUT",
                    message="收口：校验脚本缺失或被篡改，跳过自动补建，请重新生成计划",
                )
                await EventEngine().record(
                    RuntimeEventType.RECONCILIATION_GAP,
                    project_id=project_id,
                    phase="CLOSEOUT",
                    message="校验脚本无效，未自动补建交付任务",
                )
                return
            # SYS-115: 按 id 逐项跑脚本，不解析 problems 文案、禁止全表补建
            per_id_results: list[dict] = []
            for d in manifest.get("deliverables") or []:
                did = d["id"]
                one = await run_validator_script(script, ws_root, [did])
                per_id_results.append({
                    "id": did,
                    "ok": bool(one.get("ok")),
                    "problems": (one.get("problems") or [])[:6],
                })
                if not one.get("ok"):
                    missing_items.append({
                        "id": did,
                        "name": d["name"],
                        "fact": "；".join(
                            one.get("problems") or ["校验未通过"]
                        ),
                        "deliverable_ids": [did],
                        "delivery_tags": [],
                        "task_type": "交付打包",
                    })
            try:
                all_ok = not missing_items
                await EventEngine().record(
                    RuntimeEventType.DELIVERY_VALIDATOR_RUN,
                    project_id=project_id,
                    phase="CLOSEOUT",
                    message=(
                        "ok" if all_ok
                        else f"failed_ids={len(missing_items)}"
                    ),
                    payload={
                        "ok": all_ok,
                        "per_id": per_id_results[:40],
                        "problems": [
                            p for r in per_id_results
                            for p in (r.get("problems") or [])
                        ][:12],
                    },
                )
            except Exception:  # noqa: BLE001
                logger.debug("closeout RUN event failed", exc_info=True)
            if not missing_items:
                return
        elif manifest:
            for d in manifest.get("deliverables") or []:
                probs = check_match(all_names, d.get("match") or {})
                if probs:
                    missing_items.append({
                        "id": d["id"],
                        "name": d["name"],
                        "fact": "；".join(probs),
                        "deliverable_ids": [d["id"]],
                        "delivery_tags": [],
                        "task_type": "交付打包",
                    })
        elif proj.delivery_profile == "SOFTWARE_DEV":
            has_ppt = any(n.lower().endswith(".pptx") for n in all_names)
            has_code = any(
                n.lower().endswith((".py", ".js", ".ts"))
                for n in all_names
            ) and any(n.lower().startswith("readme") for n in all_names)
            has_report = any(
                "测试" in n and n.lower().endswith(".md") for n in all_names
            )
            legacy = [
                ("ppt", "方案PPT", "项目 artifact 无 .pptx 文件",
                 "交付打包"),
                ("code", "可运行代码", "项目 artifact 无代码文件或 README",
                 "开发实现"),
                ("test_report", "测试报告",
                 "项目 artifact 无含'测试'的 md 报告", "测试验证"),
            ]
            flags = {"ppt": has_ppt, "code": has_code,
                     "test_report": has_report}
            for tag, name, fact, tt in legacy:
                if not flags[tag]:
                    missing_items.append({
                        "id": tag,
                        "name": name,
                        "fact": fact,
                        "deliverable_ids": [],
                        "delivery_tags": [tag],
                        "task_type": tt,
                    })
        else:
            return

        if not missing_items:
            return

        all_tasks = list(
            (
                await session.execute(
                    select(TaskModel).where(
                        TaskModel.project_id == project_id
                    )
                )
            ).scalars().all()
        )
        existing = {t.name for t in all_tasks}
        # SYS-116: 原主责节点（playbook_ref.deliverable_ids）
        owner_by_id: dict[str, int] = {}
        for ot in all_tasks:
            try:
                pref = _json.loads(ot.playbook_ref or "{}")
            except (ValueError, TypeError):
                continue
            for did in pref.get("deliverable_ids") or []:
                owner_by_id.setdefault(str(did), ot.id)
        # 拓扑序最末的已完成交付类节点（无主责时兜底）
        deliver_fallback_id: int | None = None
        accepted_deliver = [
            t for t in all_tasks
            if t.status == TaskStatus.ACCEPTED
            and (
                (t.task_type or "") in ("交付打包", "测试验证")
                or (t.name or "").startswith("交付")
            )
        ]
        if accepted_deliver:
            deliver_fallback_id = max(accepted_deliver, key=lambda x: x.id).id
        elif all_tasks:
            done = [
                t for t in all_tasks if t.status == TaskStatus.ACCEPTED
            ]
            if done:
                deliver_fallback_id = max(done, key=lambda x: x.id).id

        created_ids: list[int] = []
        mgr = TaskGraphManager(session)
        for item in missing_items:
            task_name = f"交付补全-{item['name']}"
            await EventEngine().record(
                RuntimeEventType.RECONCILIATION_GAP,
                project_id=project_id,
                phase="CLOSEOUT",
                message=f"交付清单缺件: {task_name}（{item['fact']}）",
            )
            if task_name in existing:
                continue
            t = await mgr.create_task(CreateTaskRequest(
                project_id=project_id,
                name=task_name,
                task_type=item["task_type"],
            ))
            t.playbook_ref = _json.dumps({
                "deliverable_ids": item["deliverable_ids"],
                "delivery_tags": item["delivery_tags"],
                "stage_outputs": [item["name"]],
            }, ensure_ascii=False)
            t.contract = (
                f"交付补全任务。缺失事实：{item['fact']}。"
                f"请产出「{item['name']}」并落盘到工作区。"
            )
            # SYS-116: 补建依赖原主责（或最末已完成交付节点）
            dep_id = owner_by_id.get(str(item["id"])) or deliver_fallback_id
            if dep_id and dep_id != t.id:
                try:
                    await mgr.add_dependency(t.id, dep_id)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "backfill depends_on failed task=%s dep=%s",
                        t.id, dep_id, exc_info=True,
                    )
            try:
                from project_runtime.worker.lifecycle import (
                    provision_after_plan,
                )

                await provision_after_plan(
                    session, project_id, [item["task_type"]]
                )
            except Exception:  # noqa: BLE001
                logger.debug("backfill provision failed", exc_info=True)
            try:
                await mgr.promote_to_ready(t.id)
            except (ValueError, Exception):  # noqa: B001
                pass
            created_ids.append(t.id)
        await session.commit()
        logger.info(
            "delivery closeout: project %d missing %s — 补建任务已创建",
            project_id, [i["name"] for i in missing_items],
        )
        for tid in created_ids:
            try:
                await ExecutionService().submit(project_id, tid)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "backfill submit failed project=%s task=%s",
                    project_id, tid, exc_info=True,
                )


# 返修单-闭环接线 P0-2: 后台 runner 强引用（防 GC 丢任务）
_BACKGROUND_RUNNERS: set = set()


class ExecutionService:
    """Admission + query. No execution logic in the request lifecycle."""

    def __init__(self) -> None:
        self._runner = ExecutionRunner()

    async def submit(self, project_id: int, task_id: int) -> int:
        """Create QUEUED Execution (committed), spawn background runner, return id.

        返修单-可靠性③: 后台 runner 挂在【调用方进程】——从临时脚本/
        REPL 调用时进程一退 runner 即死（实测 exec11/12 教训）。
        仅限 runtime 进程内调用（HTTP API / lifespan）; 外部编排请走
        POST /api/projects/{id}/execute。
        """
        from project_runtime.event import RuntimeEventType

        async with AsyncSession(engine) as session:
            await assert_plan_ready_for_execution(session, project_id)
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
        # 返修单-闭环接线 P0-2: 强引用持有——asyncio.create_task 弱引用
        # 会被 GC 静默丢任务（CPython 文档明示风险; 实测 exec17 spawn 后
        # 25 分钟未消费）。模块级集合 + done 自清理（官方推荐模式）
        _bg = asyncio.create_task(self._runner.run(execution_id))
        _BACKGROUND_RUNNERS.add(_bg)
        _bg.add_done_callback(_BACKGROUND_RUNNERS.discard)
        return execution_id


def _delivery_tags_of(playbook_ref: str | None) -> list[str] | None:
    """playbook_ref 里的 delivery_tags（V1.6 序3——收工自查钩子消费）。"""
    if not playbook_ref:
        return None
    try:
        import json as _json

        tags = _json.loads(playbook_ref).get("delivery_tags")
        return list(tags) if tags else None
    except (ValueError, AttributeError, TypeError):
        return None


def _code_stage_of(playbook_ref: str | None) -> bool:
    """playbook_ref 里的 code_stage 标记（V1.5a-1——Worker 软提醒消费）。"""
    if not playbook_ref:
        return False
    try:
        import json as _json

        return bool(_json.loads(playbook_ref).get("code_stage"))
    except (ValueError, AttributeError):
        return False


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
