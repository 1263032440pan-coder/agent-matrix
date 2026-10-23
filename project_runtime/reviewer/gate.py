"""GateCore — acceptance pipeline for EXECUTION_DONE tasks.

Pipeline: EXECUTION_DONE → Artifact Collection → Validators → Gate Result.
Only GateCore may transition EXECUTION_DONE → ACCEPTED (D11-TD01 actor guard).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import GateStatus, TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Artifact, GateResult, Task

logger = logging.getLogger(__name__)


# ── Validator interface ────────────────────────────────────────────────────


@dataclass
class ValidatorResult:
    status: GateStatus  # PASS / WARNING treated as PASS, FAIL
    message: str = ""


class Validator(ABC):
    """Validators return PASS/WARNING/FAIL. They NEVER touch Task state."""

    name: str = "base"

    @abstractmethod
    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        ...


class FileValidator(Validator):
    """Real check: artifact files exist on disk and SHA-256 matches."""

    name = "file"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        if not artifacts:
            return ValidatorResult(GateStatus.FAIL, "no artifacts produced")
        for a in artifacts:
            if not os.path.exists(a.path):
                return ValidatorResult(GateStatus.FAIL, f"artifact file missing: {a.path}")
            if a.sha256:
                actual = hashlib.sha256(open(a.path, "rb").read()).hexdigest()
                if actual != a.sha256:
                    return ValidatorResult(
                        GateStatus.FAIL, f"sha256 mismatch for {a.path}"
                    )
        return ValidatorResult(GateStatus.PASS, f"{len(artifacts)} artifacts verified")


class BuildValidator(Validator):
    """V0.1 placeholder — no real build step. Always PASS."""

    name = "build"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        return ValidatorResult(GateStatus.PASS, "build skipped (V0.1)")


class TestValidator(Validator):
    """V0.1 placeholder — no real test step. Always PASS."""

    name = "test"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        return ValidatorResult(GateStatus.PASS, "tests skipped (V0.1)")


class BackfillValidator(Validator):
    """返修单v2 P2: 补建任务专属存在性 Gate（强制门——exit 2 语义）。

    范围严格限定: 仅「交付补全-*」任务（playbook_ref 带 delivery_tags
    单件标记）。查文件系统事实（不读产物文本——嘴说绕不过）:
      ppt → 项目 artifact/工作区有 .pptx
      code → 代码文件 + README
      test_report → 含'测试'的 md
    FAIL reason 可行动（防死循环配套③: 列缺失事实+工具指引, 不是
    '不合格'三个字）。FAIL → REWORK 计轮 → 五闸② 3 轮升级 ManualAction
    （人工出口）——防死循环接线（v2 §一）。
    普通 code_stage 任务零影响（不挂此 Validator）。
    """

    name = "backfill"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        import json as _json

        try:
            tags = _json.loads(task.playbook_ref or "{}").get(
                "delivery_tags"
            ) or []
        except ValueError:
            tags = []
        if not tags or not (task.name or "").startswith("交付补全"):
            return ValidatorResult(GateStatus.PASS, "not a backfill task")

        names = [a.name or "" for a in artifacts]
        problems: list[str] = []
        for tag in tags:
            if tag == "ppt":
                if not any(n.lower().endswith(".pptx") for n in names):
                    problems.append(
                        "方案PPT: 无 .pptx 文件——可用 slide_generator 生成"
                        "（不少于 6 页, 封面含项目名）"
                    )
            elif tag == "code":
                has_code = any(
                    n.lower().endswith((".py", ".js", ".ts", ".java"))
                    for n in names
                )
                has_readme = any(
                    n.lower().startswith("readme") for n in names
                )
                if not has_code:
                    problems.append(
                        "可运行代码: 无代码文件——用 file_rw 落盘 src/ 下"
                        "源码（多文件组织）"
                    )
                if not has_readme:
                    problems.append(
                        "README: 缺失——用 file_rw 落盘 README.md"
                        "（启动方式/依赖/目录结构）"
                    )
            elif tag == "test_report":
                if not any(
                    "测试" in n and n.lower().endswith(".md")
                    for n in names
                ):
                    problems.append(
                        "测试报告: 无含'测试'的 md——用 file_rw 落盘"
                        "（测试范围/结果/统计/结论）"
                    )
        if problems:
            return ValidatorResult(
                GateStatus.FAIL,
                "交付实物缺失: " + "; ".join(problems),
            )
        return ValidatorResult(
            GateStatus.PASS, f"backfill verified ({','.join(tags)})"
        )


# ── GateCore ───────────────────────────────────────────────────────────────


class GateCore:
    """Evaluates EXECUTION_DONE tasks and transitions them per Gate result.

    Actor guard (D11-TD01): only GateCore may call EXECUTION_DONE → ACCEPTED.
    """

    ACTOR = "GATE"

    def __init__(self, session: AsyncSession, validators: list[Validator] | None = None):
        self._session = session
        self._fsm = StateTransitionService(session)
        self._validators: list[Validator] = validators or [
            FileValidator(),
            BuildValidator(),
            TestValidator(),
            # 返修单v2 P2: 补建任务存在性门（非补建任务自 PASS——validate
            # 内双判断: delivery_tags + 任务名前缀, 普通任务零影响）
            BackfillValidator(),
        ]

    async def evaluate(self, task_id: int) -> GateResult:
        stmt = select(Task).where(Task.id == task_id)
        task = (await self._session.execute(stmt)).scalar_one()
        if task.status != TaskStatus.EXECUTION_DONE:
            raise ValueError(
                f"Gate requires EXECUTION_DONE, got {task.status.value}"
            )

        # WP-042: GATE_STARTED event (best-effort, never blocks the gate path)
        try:
            from project_runtime.event import EventEngine, RuntimeEventType

            await EventEngine().record(
                RuntimeEventType.GATE_STARTED,
                project_id=task.project_id,
                task_id=task_id,
                phase="GATE",
                actor=self.ACTOR,
                message=f"gate_core evaluating task {task_id}",
            )
        except Exception:  # noqa: BLE001
            pass

        # Artifact collection
        stmt = select(Artifact).where(Artifact.task_id == task.id)
        artifacts = list((await self._session.execute(stmt)).scalars().all())

        # Run validators
        details: list[str] = []
        final = GateStatus.PASS
        for v in self._validators:
            r = await v.validate(task, artifacts)
            details.append(f"[{v.name}] {r.status.value}: {r.message}")
            if r.status == GateStatus.FAIL:
                final = GateStatus.FAIL
                break  # fail fast
            if r.status == GateStatus.BLOCKED and final == GateStatus.PASS:
                final = GateStatus.BLOCKED

        # Persist GateResult
        gate_result = GateResult(
            task_id=task.id,
            gate_name="gate_core",
            status=final,
            details="\n".join(details),
        )
        self._session.add(gate_result)

        # FSM transition (actor-guarded: this is the ONLY ACCEPTED path)
        # V1.11 FSM: EXECUTION_DONE → ACCEPTED | REWORK only.
        # BLOCKED / NEED_APPROVAL both land on REWORK (GateResult.status
        # preserves the distinction for downstream ManualAction).
        if final == GateStatus.PASS:
            await self._fsm.transition_task(task, TaskStatus.ACCEPTED, actor=self.ACTOR)
        else:
            # 返修单v2 P2 防死循环接线（五闸②）: FAIL→REWORK 前查轮次——
            # 同任务 Gate FAIL 累计达 MAX_REWORK_ROUNDS(3) 不再无限打回,
            # 落 ManualAction 人工出口（WAIVED/换 Agent/用户裁决三选一）
            from sqlalchemy import func as _func
            from sqlalchemy import select as _sel

            _fails = (
                await self._session.execute(
                    _sel(_func.count()).select_from(GateResult).where(
                        GateResult.task_id == task.id,
                        GateResult.status == GateStatus.FAIL,
                    )
                )
            ).scalar() or 0
            # 当前轮 FAIL 尚未落库（add 在上面但未 flush 进 count 查询?
            # add 后同 session count 应可见——但保险起见 +1 显式计当前轮）
            _fails += 1  # 本轮 FAIL
            if _fails >= 3:
                logger.warning(
                    'Gate FAIL x%d for task %d — ManualAction escalation '
                    '(anti-deadlock fuse)', _fails, task.id,
                )
                try:
                    from project_runtime.project_state.enums import (
                        ManualActionStatus,
                    )
                    from project_runtime.project_state.models import (
                        ManualAction,
                    )

                    self._session.add(ManualAction(
                        project_id=task.project_id,
                        task_id=task.id,
                        action_type='GATE_FAIL_ESCALATION',
                        status=ManualActionStatus.PENDING,
                        proposal_payload=json.dumps({
                            'reason': 'Gate 连续 3 轮 FAIL（防死循环熔断）',
                            'options': [
                                'WAIVED: 用户豁免验收, 任务直接通过',
                                'RETRY: 换 Agent 重试',
                                'CANCEL: 终止任务',
                            ],
                            'last_details': '\n'.join(details)[:500],
                        }, ensure_ascii=False),
                    ))
                except Exception:  # noqa: BLE001 — 升级 best-effort
                    logger.debug('escalation failed', exc_info=True)
            await self._fsm.transition_task(task, TaskStatus.REWORK, actor=self.ACTOR)

        await self._session.flush()
        logger.info("Gate %s → %s for task %d", "gate_core", final.value, task_id)

        # WP-042: GATE_PASSED / GATE_FAILED event — inserted in the CALLER's
        # transaction (same session) so it commits atomically with the Gate
        # result. A separate EventEngine session here would contend with the
        # still-open write tx (SQLite single-writer) and the event was lost.
        try:
            from project_runtime.event.models import RuntimeEvent, RuntimeEventType

            message = f"gate_core → {final.value}"
            if len(message) > 120:  # event message budget (RH-03 修改单瑕疵 1)
                message = message[:120] + "…"
            self._session.add(
                RuntimeEvent(
                    event_type=(
                        RuntimeEventType.GATE_PASSED.value
                        if final == GateStatus.PASS
                        else RuntimeEventType.GATE_FAILED.value
                    ),
                    project_id=task.project_id,
                    task_id=task_id,
                    phase="GATE",
                    actor=self.ACTOR,
                    message=message,
                    payload=json.dumps(
                        {"details": "\n".join(details)[:500]}, ensure_ascii=False
                    ),
                )
            )
            await self._session.flush()
        except Exception:  # noqa: BLE001 — event is best-effort
            logger.debug("gate terminal event failed", exc_info=True)

        return gate_result
