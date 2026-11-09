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
    """Real check: artifact files exist on disk and SHA-256 matches.

    返修单-REWORK轮sha256误拦: REWORK 链（gate_result 有 FAIL 史）上
    文件被重写是修复行为本体——hash 不匹配降级 WARNING + 刷新工单
    （工单格式 REFRESH:<abs_path>:<new_hash>, GateCore 消费）;
    首轮/正常链维持 FAIL（防篡改语义不回退）。
    Validator 不写 DB（SYS-063 契约）——刷新由 GateCore 落。
    """

    name = "file"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        if not artifacts:
            return ValidatorResult(GateStatus.FAIL, "no artifacts produced")
        # REWORK 链判定: 该任务有过 FAIL 史（由 GateCore 注入——
        # Validator 无 session, 经 artifacts 参数外的注入槽传）
        in_rework_chain = bool(getattr(task, "_gate_fail_history", False))
        refresh_orders: list[str] = []
        for a in artifacts:
            if not os.path.exists(a.path):
                _rel = _relpath(a.path)
                return ValidatorResult(
                    GateStatus.FAIL, f"artifact file missing: {_rel}"
                )
            if a.sha256:
                actual = hashlib.sha256(
                    open(a.path, "rb").read()
                ).hexdigest()
                if actual != a.sha256:
                    _rel = _relpath(a.path)
                    if in_rework_chain:
                        refresh_orders.append(
                            f"REFRESH:{a.path}:{actual}"
                        )
                    else:
                        return ValidatorResult(
                            GateStatus.FAIL,
                            f"sha256 mismatch for {_rel}",
                        )
        if refresh_orders:
            return ValidatorResult(
                GateStatus.WARNING,
                f"{len(artifacts)} verified; {len(refresh_orders)} file(s) "
                f"rewritten in rework round (hash refresh due): "
                + "; ".join(_relpath(o.split(":")[1]) for o in refresh_orders)
                + " || " + "|".join(refresh_orders),
            )
        return ValidatorResult(
            GateStatus.PASS, f"{len(artifacts)} artifacts verified"
        )


def _relpath(p: str) -> str:
    """返修单连带项: Gate details 路径相对化（防截断丢关键信息）。"""
    try:
        from pathlib import Path as _Pth

        return str(_Pth(p).resolve().relative_to(_Pth("data/tasks").resolve()))
    except ValueError:
        return p


def _pptx_quality_check(pptx_arts: list) -> str:
    """返修单 V3 批D-D4: 读回 pptx 事实断言（零 AI）。

    规则: 页数≥6、非空页占比≥80%、封面有字。
    返回空串=通过；非空=可执行 FAIL 指引。
    """
    path = None
    for a in pptx_arts:
        p = getattr(a, "path", None) or ""
        if p and os.path.exists(p):
            path = p
            break
    if not path:
        return (
            "方案PPT: 有 .pptx 登记但文件不可读——请确认产物已落盘后"
            "用 office_pptx 重生成（≥6 页、非空≥80%、封面有字）"
        )
    try:
        from pptx import Presentation as _Pres

        slides = list(_Pres(path).slides)
    except Exception as e:  # noqa: BLE001
        return (
            f"方案PPT: 无法读回 {_relpath(path)}（{e}）——"
            "请用 office_pptx 重生成合规大纲（每页一个 ##）"
        )
    n = len(slides)
    if n < 6:
        return (
            f"方案PPT: 仅 {n} 页（要求≥6）——请用 office_pptx 按"
            "`##` 一节一页重生成（建议 6-12 页，封面用 `#`）"
        )
    non_empty = sum(
        1
        for sl in slides
        if any(
            sh.has_text_frame and sh.text_frame.text.strip()
            for sh in sl.shapes
        )
    )
    ratio = non_empty / n if n else 0.0
    if ratio < 0.8:
        return (
            f"方案PPT: 非空页 {non_empty}/{n}（占比 {ratio:.0%} < 80%）——"
            "空壳页过多，请按一节一页大纲用 office_pptx 重生成"
        )
    cover_ok = bool(
        slides
        and any(
            sh.has_text_frame and sh.text_frame.text.strip()
            for sh in slides[0].shapes
        )
    )
    if not cover_ok:
        return (
            "方案PPT: 封面无文字——请用 `# 项目名` 作封面后"
            "经 office_pptx 重生成"
        )
    return ""


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

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def _project_workspace(self, task: Task):
        """项目验收根：自定义 workspace_path 优先，否则 data/tasks/{pid}。"""
        from project_runtime.workspace import resolve_project_workspace

        ws_path = getattr(task, "workspace_path", None)
        if ws_path is None and self._session is not None:
            from project_runtime.project_state.models import Project

            proj = await self._session.get(Project, int(task.project_id))
            if proj is not None:
                ws_path = getattr(proj, "workspace_path", None)
        return resolve_project_workspace(int(task.project_id), ws_path)

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        import json as _json

        try:
            ref = _json.loads(task.playbook_ref or "{}")
        except ValueError:
            ref = {}
        tags = list(ref.get("delivery_tags") or [])
        ids = [str(x) for x in (ref.get("deliverable_ids") or []) if x]
        if not (task.name or "").startswith("交付补全"):
            return ValidatorResult(GateStatus.PASS, "not a backfill task")
        if not tags and not ids:
            return ValidatorResult(GateStatus.PASS, "not a backfill task")

        # V1.6d: deliverable_ids → 与 Stop 同口径（冻结脚本）
        if ids:
            from project_runtime.procedure.delivery_validator import (
                run_validator_script,
                validator_path,
            )

            script = validator_path(int(task.project_id))
            # 与 Worker Stop / 自定义工作区同口径（禁止写死 data/tasks/{pid}）
            ws = await self._project_workspace(task)
            if not script.exists():
                return ValidatorResult(
                    GateStatus.FAIL,
                    "交付校验脚本缺失——请重新生成计划后再跑补全任务",
                )
            result = await run_validator_script(script, ws, ids)
            if result.get("ok"):
                return ValidatorResult(
                    GateStatus.PASS,
                    f"backfill verified (ids={','.join(ids)})",
                )
            problems = list(result.get("problems") or ["校验未通过"])
            return ValidatorResult(
                GateStatus.FAIL,
                "交付实物缺失: " + "; ".join(problems),
            )

        names = [a.name or "" for a in artifacts]
        problems: list[str] = []
        for tag in tags:
            if tag == "ppt":
                pptx_arts = [
                    a for a in artifacts
                    if (a.name or "").lower().endswith(".pptx")
                ]
                if not pptx_arts:
                    problems.append(
                        "方案PPT: 无 .pptx 文件——可用 office_pptx 生成"
                        "（不少于 6 页, 封面含项目名）"
                    )
                else:
                    # 返修单 V3 批D-D4: 读回断言（页数/非空占比/封面）
                    q = _pptx_quality_check(pptx_arts)
                    if q:
                        problems.append(q)
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
                        "可运行代码: 无代码文件——用 write_file 落盘 src/ 下"
                        "源码（多文件组织）"
                    )
                if not has_readme:
                    problems.append(
                        "README: 缺失——用 write_file 落盘 README.md"
                        "（启动方式/依赖/目录结构）"
                    )
            elif tag == "test_report":
                if not any(
                    "测试" in n and n.lower().endswith(".md")
                    for n in names
                ):
                    problems.append(
                        "测试报告: 无含'测试'的 md——用 write_file 落盘"
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
            BackfillValidator(session=session),
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

        # SYS-138: 软放行——最新 TaskRun 带 delivery_gap.soft_accepted
        # → PASS_WITH_GAP，跳过会把缺口打成 FAIL 的校验器
        soft_gap: dict | None = None
        try:
            from sqlalchemy import select as _sel_tr

            from project_runtime.project_state.models import TaskRun as TaskRunRow

            _runs = (
                await self._session.execute(
                    _sel_tr(TaskRunRow)
                    .where(TaskRunRow.task_id == task.id)
                    .order_by(TaskRunRow.id.desc())
                    .limit(1)
                )
            ).scalars().all()
            if _runs and _runs[0].result:
                import json as _json

                _payload = _json.loads(_runs[0].result)
                _gap = (_payload or {}).get("delivery_gap")
                if isinstance(_gap, dict) and _gap.get("soft_accepted"):
                    soft_gap = _gap
        except Exception:  # noqa: BLE001
            soft_gap = None

        details: list[str] = []
        final = GateStatus.PASS
        from sqlalchemy import select as _sel

        if soft_gap is not None:
            reason = str(soft_gap.get("reason") or "DELIVERY_GAP")
            details.append(
                f"[delivery_gap] PASS_WITH_GAP: {reason}; "
                f"problems={soft_gap.get('problems') or []}"
            )
            final = GateStatus.PASS
        else:
            # 返修单-REWORK轮sha256误拦: REWORK 链判定注入（该任务 FAIL 史）
            _fail_hist = (
                await self._session.execute(
                    _sel(GateResult.id).where(
                        GateResult.task_id == task.id,
                        GateResult.status == GateStatus.FAIL,
                    )
                )
            ).first()
            task._gate_fail_history = _fail_hist is not None
            for v in self._validators:
                r = await v.validate(task, artifacts)
                details.append(f"[{v.name}] {r.status.value}: {r.message}")
                if r.status == GateStatus.FAIL:
                    final = GateStatus.FAIL
                    break  # fail fast
                if r.status == GateStatus.BLOCKED and final == GateStatus.PASS:
                    final = GateStatus.BLOCKED

        # 返修单-REWORK轮sha256误拦: 消费 FileValidator 的刷新工单——
        # WARNING/PASS 落定后, 对 mismatch 文件把 artifact.sha256 刷成
        # 磁盘实际值（记账跟上现实; GateCore 有 session 有记账权）
        import re as _re

        for _line in details:
            for _order in _re.findall(r"REFRESH:[^|;]+", _line):
                try:
                    _, _p, _h = _order.split(":", 2)
                    from sqlalchemy import update as _upd

                    await self._session.execute(
                        _upd(Artifact)
                        .where(Artifact.path == _p)
                        .values(sha256=_h)
                    )
                    logger.info(
                        "rework hash refresh: %s", _relpath(_p)
                    )
                except Exception:  # noqa: BLE001 — 刷新 best-effort
                    logger.debug("hash refresh failed", exc_info=True)

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
        if final in (GateStatus.PASS, GateStatus.WARNING):
            # WARNING treated as PASS（REWORK 轮重写文件的合法放行——
            # sha256 已由上面的工单消费刷新, 下轮校验自然干净）
            await self._fsm.transition_task(task, TaskStatus.ACCEPTED, actor=self.ACTOR)
            # SYS-144: 用户重开挂起的连带下游 → user_rework_release（非心跳）
            try:
                from project_runtime.execution.user_rework import (
                    release_cascaded_after_accept,
                )

                await release_cascaded_after_accept(
                    self._session,
                    task.project_id,
                    task_id,
                    submit=False,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "release cascaded after accept failed", exc_info=True,
                )
            if soft_gap is not None:
                try:
                    from project_runtime.event.models import (
                        RuntimeEvent,
                        RuntimeEventType,
                    )

                    self._session.add(
                        RuntimeEvent(
                            event_type=RuntimeEventType.DELIVERY_GAP_ACCEPTED.value,
                            project_id=task.project_id,
                            task_id=task_id,
                            phase="GATE",
                            actor=self.ACTOR,
                            message=(
                                f"soft-accept {soft_gap.get('reason')}"
                            )[:120],
                            payload=json.dumps(
                                {
                                    "reason": soft_gap.get("reason"),
                                    "scan_root": soft_gap.get("scan_root"),
                                    "problems": (
                                        soft_gap.get("problems") or []
                                    )[:8],
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("emit DELIVERY_GAP_ACCEPTED failed", exc_info=True)
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
