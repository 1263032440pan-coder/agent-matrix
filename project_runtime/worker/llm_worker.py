"""LLMWorker — real Worker execution via LiteLLM (批次 A).

Red lines (inherited from MockWorkerAdapter):
  1. Worker can only transition RUNNING → EXECUTION_DONE.
  2. Worker MUST NOT directly mark ACCEPTED.
  3. Worker can only start from READY status.

Execution:
  - Task Contract (name + playbook_ref + optional Skill context) → LLM prompt
  - LLM output → Artifact file + TaskRun audit
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.adapter import ExecutionResult, WorkerAdapter, WorkPackage

if TYPE_CHECKING:
    from project_runtime.artifact.manager import ArtifactManager

logger = logging.getLogger(__name__)

# 全局工具循环上限：全部走本 Worker 的任务统一 32，无按节点例外。
_TOOL_ROUNDS = 32

# V1.6b: Claude Stop 对齐——交付存在性硬闸独立返工轮次（仅提前收工路径）
_DELIVERY_REPAIR_MAX = 3

_BUDGET_GRACE_NUDGE = (
    "【Continue nudge·工具轮次已用尽】请直接输出最终交付说明"
    "（做到哪、缺什么），不要再调用工具。"
)

# 思维链语言：与规划侧一致，强制中文 reasoning（不影响工具/代码标识符）
_WORKER_ROLE_PROMPT = (
    "你是项目开发智能体，按任务契约交付。"
    "历史任务卡片供参考，不要复述。"
    "推理与思维链一律使用中文；交付正文直接给结果，勿把思考过程写入正式回复。"
)
_WORKER_TASK_TRAILER = (
    "请完成上述任务，输出具体的交付内容（markdown 格式）。"
    "正式回复（content）不要输出思考过程，直接给出结果。"
    "内部推理与思维链（reasoning）一律使用中文；"
    "代码、路径、工具参数保持原样（可为英文）。"
)


class DeliveryBaselineError(RuntimeError):
    """交付基线校验失败（篡改等硬故障）——Worker 应 success=False → Task FAILED。"""


class DeliveryGapSoftAcceptError(Exception):
    """预算用尽/返工用尽仍缺件——软放行：success=True → EXECUTION_DONE → Gate PASS_WITH_GAP。"""

    def __init__(self, message: str, *, delivery_gap: dict):
        super().__init__(message)
        self.delivery_gap = dict(delivery_gap or {})
        self.delivery_gap.setdefault("soft_accepted", True)


def _delivery_tags_for_wp(wp) -> list[str]:
    tags = list(getattr(wp, "delivery_tags", None) or [])
    if getattr(wp, "code_stage", False) and "code" not in tags:
        tags.append("code")
    return tags


def _check_delivery_workspace(ws_root: Path, tags: list[str]) -> list[str]:
    """工作区存在性校验（与收口/BackfillValidator 同口径）。返回问题列表，空=通过。"""
    names: list[str] = []
    if ws_root.exists():
        names = [p.name for p in ws_root.rglob("*") if p.is_file()]
    problems: list[str] = []
    for tag in tags:
        if tag == "ppt":
            if not any(n.lower().endswith(".pptx") for n in names):
                problems.append(
                    "方案PPT: 工作区无 .pptx——请用 office_pptx 生成"
                    "（不少于 6 页，封面含项目名）"
                )
        elif tag == "code":
            has_code = any(
                n.lower().endswith((".py", ".js", ".ts", ".java"))
                for n in names
            )
            has_readme = any(n.lower().startswith("readme") for n in names)
            if not has_code:
                problems.append(
                    "可运行代码: 无代码文件——请用 write_file 落盘源码"
                )
            if not has_readme:
                problems.append(
                    "README: 缺失——请用 write_file 落盘 README.md"
                    "（启动方式/依赖/目录结构）"
                )
        elif tag == "test_report":
            if not any(
                "测试" in n and n.lower().endswith(".md") for n in names
            ):
                problems.append(
                    "测试报告: 无含「测试」的 md——"
                    "请用 write_file 落盘（范围/结果/统计/结论）"
                )
    return problems


def _repair_arguments(raw: str) -> str | None:
    """tool_calls.arguments 流式拼装完整性校验与尾部修复（V1.4i 防线1）。

    长 content 的 arguments 由几十个流式分片拼成——任何分片丢失/乱序
    都会拼出断裂 JSON（实测: 模型 write 数千字文档时必触发）。
    修复策略: 尾部截断（最主要形态）尝试补闭合符; 中段丢块无法猜——
    返回 None（调用层给模型可理解的错误文案让其自救）。
    """
    text = (raw or "").strip()
    if not text:
        return "{}"
    try:
        json.loads(text)
        return text  # 完整——直接用
    except ValueError:
        pass
    # 尾部截断形态: 补闭引号/闭括号（按命中率排序）
    for suffix in ('"}', '"}', '"}', '}', '"'):
        try:
            json.loads(text + suffix)
            return text + suffix
        except ValueError:
            continue
    # 特殊形态: 字符串值中间截断且含转义——补闭引号前剥离悬挂反斜杠
    if text.endswith(chr(92)):
        try:
            json.loads(text[:-1] + '"}')
            return text[:-1] + '"}'
        except ValueError:
            pass
    return None  # 中段丢块——无法修复


def _extract_planned_paths(content: str) -> list[str] | None:
    """从首轮回复里抽出相对路径 JSON 数组；没有则 None。"""
    text = (content or "").strip()
    if not text:
        return None
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    if not all(isinstance(x, str) for x in raw):
        return None
    return [str(x).replace("\\", "/") for x in raw]


def _tool_result_text(result: dict) -> str:
    """TOOL 结果 → 喂回模型的文本；结构化失败固定为 [code] error + 建议。"""
    if not result.get("ok") and result.get("error_code"):
        text = f"[{result['error_code']}] {result.get('error') or ''}"
        if result.get("hint"):
            text += f"\n建议：{result['hint']}"
        return text[:6000]
    return str(result.get("stdout")
               or result.get("result")
               or result.get("error")
               or "")[:6000]



class LLMWorkerAdapter(WorkerAdapter):
    """Real worker: executes tasks via LLM (LiteLLM)."""

    def __init__(
        self,
        session: AsyncSession,
        artifact_mgr: "ArtifactManager | None" = None,
        max_tokens: int = 16000,
    ):
        self._session = session
        self._fsm = StateTransitionService(session)
        self._mgr = TaskGraphManager(session)
        self._artifact_mgr = artifact_mgr
        self._max_tokens = max_tokens
        # V1.5a-1 序6: 工作区快照状态
        self._current_task_id: int | None = None
        # V1.6b: 交付硬闸返工计数（task_id → 已注入次数；仅提前收工）
        self._delivery_repair_counts: dict[int, int] = {}
        # V1.6 序0: 任务工作区根（文件工具写禁闭）
        self._workspace_root: str | None = None
        # 返修单 B: 当前 Agent 归属（实时思维链事件 payload）
        self._current_agent: dict | None = None
        self._current_project_id: int | None = None
        self._db_lock = asyncio.Lock()
        # SYS-152～157
        self._obligation_tokens: list[dict] = []
        self._tool_failures: list[dict] = []
        self._diagnosis_injected: int = 0

    async def assign(self, wp: WorkPackage) -> ExecutionResult:
        # Phase 1 (short tx): validate READY + RUNNING transition, commit
        # immediately — the LLM call must not hold a write transaction (WP-041
        # §3 / DB-04). Previously flush()-only kept the tx open across the
        # network call.
        from infrastructure.database.short_tx import commit_with_retry

        async def _phase1_stage() -> None:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")
            # Cache project_id once — CoT emit used to SELECT per chunk.
            self._current_project_id = task.project_id
            if task.status == TaskStatus.RUNNING:
                # Idempotent after lock-retry: transition already persisted or
                # re-applied in this session.
                self._current_task_id = task.id
                self._workspace_root = getattr(wp, "workspace_root", None)
                return
            if task.status != TaskStatus.READY:
                raise ValueError(
                    f"Worker cannot start task in status {task.status.value}. Must be READY."
                )
            self._current_task_id = task.id
            self._workspace_root = getattr(wp, "workspace_root", None)
            await self._fsm.transition_task(task, TaskStatus.RUNNING)

        async with self._db_lock:
            await _phase1_stage()
            await commit_with_retry(
                self._session,
                context="llm_worker.phase1",
                on_retry=_phase1_stage,
            )

        # V1.5a-1 序6: 工作区快照（收工 diff 用——新增文件登记 Artifact）
        ws_snapshot = self._workspace_snapshot()

        # Execute via LLM (zero DB transaction held — network call)
        # 软放行：DeliveryGapSoftAcceptError → success=True + delivery_gap 进 TaskRun
        # 硬故障：DeliveryBaselineError → FAILED
        delivery_gap: dict | None = None
        try:
            output = await self._execute_llm(wp)
            success = True
        except DeliveryGapSoftAcceptError as e:
            logger.warning(
                "Delivery gap soft-accept task %d: %s", wp.task_id, e,
            )
            output = str(e)
            success = True
            delivery_gap = dict(e.delivery_gap)
        except DeliveryBaselineError as e:
            logger.error(
                "Delivery baseline failed for task %d: %s", wp.task_id, e
            )
            output = str(e)
            success = False
        except Exception as e:
            logger.error("LLM execution failed for task %d: %s", wp.task_id, e)
            output = f"LLM execution failed: {e}"
            success = False

        # V1.5a-1 序6: 快照 diff 在官方产物落盘**前**算好——
        # create_artifact 会把 {任务名}_output.md 写进工作区，diff 若在
        # 其后算会把官方产物再登记一遍（双登记，e2e 4≠3 的根因）。
        # 文件系统操作不进 Phase 2 锁——顺带缩小锁窗口。
        harvested_files: list[Path] = []
        if success:
            try:
                harvested_files = self._workspace_diff(ws_snapshot)
            except Exception:  # noqa: BLE001
                logger.debug("workspace diff failed", exc_info=True)

        # Phase 2 (short tx): re-fetch task (Phase-1 commit expired it),
        # EXECUTION_DONE/FAILED + TaskRun + Artifact, then commit.
        artifact_id: int | None = None
        cancelled = False

        async def _phase2_stage() -> None:
            nonlocal artifact_id, cancelled
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")
            if task.status == TaskStatus.CANCELLED:
                cancelled = True
                return
            # Idempotent: already terminal after a prior partial commit.
            if task.status in (TaskStatus.EXECUTION_DONE, TaskStatus.FAILED):
                return
            if success:
                await self._fsm.transition_task(task, TaskStatus.EXECUTION_DONE)
            else:
                await self._fsm.transition_task(task, TaskStatus.FAILED)
            await self._session.flush()

            from project_runtime.project_state.models import TaskRun

            now = datetime.datetime.now(datetime.timezone.utc)
            run_payload: dict = {
                "output": output[:2000],
                "run_mode": "llm",
            }
            if delivery_gap:
                run_payload["delivery_gap"] = delivery_gap
            if wp.skill_context is not None:
                run_payload["skill"] = {
                    "skill_id": wp.skill_context.skill_id,
                    "skill_version": wp.skill_context.skill_version,
                    "content_hash": wp.skill_context.skill_content_hash,
                }
            self._session.add(TaskRun(
                task_id=task.id,
                attempt=1,
                started_at=now,
                finished_at=now,
                result=json.dumps(run_payload, ensure_ascii=False),
            ))

            if success and self._artifact_mgr is not None:
                artifact = await self._artifact_mgr.create_artifact(
                    project_id=task.project_id,
                    task_id=task.id,
                    logical_id=f"task-{task.id}-output",
                    name=f"{task.name}_output.md",
                    content=output,
                )
                await self._session.flush()
                artifact_id = artifact.id

            if success and self._artifact_mgr is not None and harvested_files:
                try:
                    await self._artifact_mgr.batch_register_from_workspace(
                        project_id=task.project_id,
                        task_id=task.id,
                        files=harvested_files,
                    )
                except Exception:  # noqa: BLE001 — harvest 绝不阻断收工
                    logger.debug("workspace harvest failed", exc_info=True)

        async with self._db_lock:
            await _phase2_stage()
            if cancelled:
                return ExecutionResult(
                    task_id=wp.task_id, success=False, output="cancelled"
                )
            await commit_with_retry(
                self._session,
                context="llm_worker.phase2",
                on_retry=_phase2_stage,
            )

        return ExecutionResult(
            task_id=wp.task_id,
            success=success,
            output=output[:500],
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

    # ── V1.5a-1 序6/7: 工作区快照 + diff + 软提醒 ─────────────────

    def _workspace_snapshot(self) -> set[str]:
        """任务开始时的工作区文件集（相对路径字符串）。"""
        root = self._task_ws_root()
        if not root.exists():
            return set()
        return {
            str(p.relative_to(root))
            for p in root.rglob("*") if p.is_file()
        }

    def _workspace_diff(self, snapshot: set[str]) -> list[Path]:
        """收工时的新增/变更文件（快照里没有的 = 新增）。"""
        root = self._task_ws_root()
        if not root.exists():
            return []
        return [
            p for p in root.rglob("*")
            if p.is_file() and str(p.relative_to(root)) not in snapshot
        ]

    def _task_ws_root(self) -> Path:
        """本任务的工作区根（优先 WP.workspace_root）。"""
        if self._workspace_root:
            return Path(self._workspace_root)
        if self._artifact_mgr is not None:
            return Path(self._artifact_mgr._workspace_root) / str(  # noqa: SLF001
                self._current_task_id or ""
            )
        return Path(f"data/tasks/0/{self._current_task_id or 0}")

    async def _delivery_stop_problems(self, wp) -> list[str]:
        """兼容旧调用：仅返回 problems 文案列表。"""
        report = await self._delivery_stop_report(wp)
        return list(report.problems)

    async def _delivery_stop_report(self, wp):
        """V1.6d + SYS-139: 收工硬闸报告（problems/unmet/scan_*）。

        脚本缺失/hash 不符 → DeliveryBaselineError（不降级、不软放行）。
        """
        import json as _json
        import time

        from project_runtime.procedure.delivery_validator import (
            run_validator_script,
            sha256_file,
            validator_path,
        )
        from project_runtime.procedure.token_stop import (
            ObligationStopResult,
            check_obligation_tokens_detailed,
        )

        ws = self._task_ws_root()
        wr = getattr(wp, "workspace_root", None)
        if wr:
            ws = Path(wr)

        ref: dict = {}
        try:
            raw_ref = getattr(wp, "playbook_ref", None)
            if raw_ref:
                ref = _json.loads(raw_ref)
        except (ValueError, TypeError):
            ref = {}
        ids = [str(x) for x in (ref.get("deliverable_ids") or []) if x]
        tags = _delivery_tags_for_wp(wp)
        tokens = list(ref.get("obligation_tokens") or [])

        pid = getattr(self, "_current_project_id", None)
        proj = None
        if pid is not None and self._session is not None:
            try:
                from project_runtime.project_state.models import Project

                proj = await self._session.get(Project, pid)
            except Exception:  # noqa: BLE001
                logger.debug("load project for validator failed", exc_info=True)

        if tokens:
            deliverables_by_id: dict = {}
            if proj is not None and proj.delivery_manifest:
                try:
                    from project_runtime.procedure.delivery_manifest import (
                        validate_manifest,
                    )

                    man = validate_manifest(proj.delivery_manifest)
                    deliverables_by_id = {
                        str(d["id"]): d
                        for d in (man.get("deliverables") or [])
                    }
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "manifest for token stop failed", exc_info=True,
                    )
            return check_obligation_tokens_detailed(
                ws,
                tokens,
                deliverables_by_id=deliverables_by_id,
                scan_path=str(ws).replace("\\", "/"),
            )

        if ids and proj is not None and pid is not None:
            expected = proj.delivery_validator_hash
            script = validator_path(int(pid))
            actual = sha256_file(script) if script.exists() else None
            if not expected or actual != expected:
                try:
                    from project_runtime.event import (
                        EventEngine,
                        RuntimeEventType,
                    )

                    await EventEngine().record(
                        RuntimeEventType.DELIVERY_VALIDATOR_TAMPERED,
                        project_id=int(pid),
                        task_id=int(getattr(wp, "task_id", 0) or 0),
                        phase="STOP",
                        message="校验脚本缺失或被篡改，请重新生成计划",
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("emit TAMPERED failed", exc_info=True)
                raise DeliveryBaselineError(
                    "交付校验脚本缺失或被篡改，请重新生成计划"
                )
            if self._artifact_mgr is not None:
                project_root = Path(
                    self._artifact_mgr._workspace_root  # noqa: SLF001
                )
            else:
                project_root = Path(f"data/tasks/{int(pid)}")
            t0 = time.monotonic()
            result = await run_validator_script(script, project_root, ids)
            elapsed = int((time.monotonic() - t0) * 1000)
            try:
                from project_runtime.event import (
                    EventEngine,
                    RuntimeEventType,
                )

                await EventEngine().record(
                    RuntimeEventType.DELIVERY_VALIDATOR_RUN,
                    project_id=int(pid),
                    task_id=int(getattr(wp, "task_id", 0) or 0),
                    phase="STOP",
                    message=(
                        "ok" if result.get("ok")
                        else ";".join(result.get("problems") or [])[:100]
                    ),
                    payload={
                        "ok": bool(result.get("ok")),
                        "problems": (result.get("problems") or [])[:8],
                        "elapsed_ms": elapsed,
                        "scan_root": "project",
                    },
                )
            except Exception:  # noqa: BLE001
                logger.debug("emit VALIDATOR_RUN failed", exc_info=True)
            problems = [] if result.get("ok") else list(
                result.get("problems") or ["校验未通过"]
            )
            unmet = [
                {"token_id": None, "kind": "manifest", "hint": p}
                for p in problems
            ]
            return ObligationStopResult(
                problems=problems,
                unmet=unmet,
                scan_root="project",
                scan_path=str(project_root).replace("\\", "/"),
            )

        if tags:
            problems = _check_delivery_workspace(ws, tags)
            unmet = [
                {"token_id": None, "kind": "tag", "hint": p}
                for p in problems
            ]
            return ObligationStopResult(
                problems=problems,
                unmet=unmet,
                scan_root="task",
                scan_path=str(ws).replace("\\", "/"),
            )
        return ObligationStopResult(
            scan_root="task",
            scan_path=str(ws).replace("\\", "/"),
        )

    def _integrity_stop_action(
        self, report, *, allow_repair: bool = True,
    ) -> tuple[str, object] | None:
        """存在性通过后的完整性闸。返回 ('repair', nudge) | ('soft', kwargs) | None。"""
        from pathlib import Path as _Path

        from project_runtime.procedure.artifact_integrity import (
            check_paths_integrity,
            collect_scan_rel_paths,
            integrity_repair_nudge,
        )
        from project_runtime.procedure.token_stop import ObligationStopResult

        scan = str(getattr(report, "scan_path", "") or "").strip()
        if not scan:
            return None
        root = _Path(scan)
        failures = check_paths_integrity(root, collect_scan_rel_paths(root))
        if not failures:
            return None
        problems = [
            f"假/损坏：{f['path']}（{f.get('detail') or f.get('kind')}）"
            for f in failures
        ]
        soft_report = ObligationStopResult(
            problems=problems,
            unmet=[
                {"token_id": None, "kind": "integrity", "hint": p}
                for p in problems
            ],
            scan_root=getattr(report, "scan_root", "task"),
            scan_path=scan,
        )
        tid = int(getattr(self, "_current_task_id", 0) or 0)
        used = self._delivery_repair_counts.get(tid, 0)
        if allow_repair and used < _DELIVERY_REPAIR_MAX:
            self._delivery_repair_counts[tid] = used + 1
            return (
                "repair",
                integrity_repair_nudge(
                    used, failures, max_repairs=_DELIVERY_REPAIR_MAX,
                ),
            )
        return (
            "soft",
            {
                "reason": "CONTENT_INTEGRITY_SOFT",
                "report": soft_report,
                "message": (
                    "内容完整性未通过（软放行）：" + "；".join(problems)
                ),
                "extra": {
                    "ui_visibility": "audit_only",
                    "integrity_failures": failures,
                },
            },
        )

    def _raise_delivery_gap_soft(
        self,
        *,
        reason: str,
        report,
        message: str,
        tool_rounds: int | None = None,
        extra: dict | None = None,
    ) -> None:
        import datetime as _dt

        gap = {
            "reason": reason,
            "soft_accepted": True,
            "problems": list(report.problems),
            "unmet": list(report.unmet),
            "scan_root": report.scan_root,
            "scan_path": report.scan_path,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        if tool_rounds is not None:
            gap["tool_rounds"] = tool_rounds
        if extra:
            gap.update(extra)
        raise DeliveryGapSoftAcceptError(message, delivery_gap=gap)

    async def cancel(self, task_id: int) -> None:
        async with self._db_lock:
            task = await self._mgr.get_task(task_id)
            if task is None:
                return
            if task.status == TaskStatus.RUNNING:
                await self._fsm.transition_task(task, TaskStatus.CANCELLED)
                await self._session.flush()

    async def health(self) -> str:
        from project_runtime.llm import load_llm_settings

        cfg = load_llm_settings()
        if cfg.base_url and cfg.api_key and cfg.model:
            return "healthy"
        return "llm_not_configured"

    # ── 返修单 A 层①②: 断流重连单元（网关长流偶发掐断的客户端韧性）──

    _STREAM_RETRY_EXC = (
        "APIConnectionError",
        "MidStreamFallbackError",
        "TimeoutError",
        "ConnectionError",
    )

    @staticmethod
    def _is_stream_failure(e: Exception) -> bool:
        """网关断流类异常判定（按类名——litellm 异常跨版本稳定面）。"""
        name = type(e).__name__
        return any(k in name for k in (
            "APIConnection", "MidStream", "Timeout",
        )) or isinstance(e, (TimeoutError, ConnectionError))

    async def _stream_round(
        self, cfg, model: str, messages: list, tools,
    ):
        """单轮 LLM 调用（流式）——返回 result dict。"""
        from project_runtime.llm.provider import llm_acomplete

        return await llm_acomplete(
            model=model,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=messages,
            max_tokens=self._max_tokens,
            timeout=600,
            stream=True,
            **({"tools": tools} if tools else {}),
            **({"tool_choice": "auto"} if tools else {}),
        )

    async def _execute_llm(self, wp: WorkPackage) -> str:
        from project_runtime.llm import (
            load_llm_settings,
            normalize_model_for_gateway,
        )

        # AOP-P2-2 清偿: litellm 直调 → LLM Capability Provider

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            raise RuntimeError("LLM not configured")
        self._tool_failures = []
        self._diagnosis_injected = 0
        self._plan_list_checked = False

        model = normalize_model_for_gateway(cfg.model, cfg.base_url)

        # Build prompt from task contract + optional skill
        prompt_parts = [f"任务：{wp.task_name}"]
        if wp.playbook_ref:
            prompt_parts.append(f"上下文：{wp.playbook_ref}")
        if wp.contract:
            prompt_parts.append(f"契约：{wp.contract}")
        if wp.skill_context:
            prompt_parts.append(f"方法指导：\n{wp.skill_context.skill_body[:3000]}")
        # SYS-137: slot:/count: → 必写槽目录（分片仅有 count 时也要提示）
        slot_dirs: list[str] = []
        try:
            from project_runtime.procedure.token_stop import (
                slot_dirs_for_tokens,
            )

            raw_ref = getattr(wp, "playbook_ref", None)
            ref = json.loads(raw_ref) if isinstance(raw_ref, str) else (
                raw_ref if isinstance(raw_ref, dict) else {}
            )
            tokens = list((ref or {}).get("obligation_tokens") or [])
            deliverables_by_id: dict = {}
            pid = getattr(self, "_current_project_id", None)
            if pid is not None and self._session is not None:
                from project_runtime.project_state.models import Project

                proj = await self._session.get(Project, pid)
                if proj is not None and proj.delivery_manifest:
                    from project_runtime.procedure.delivery_manifest import (
                        validate_manifest,
                    )

                    man = validate_manifest(proj.delivery_manifest)
                    deliverables_by_id = {
                        str(d["id"]): d
                        for d in (man.get("deliverables") or [])
                    }
            slot_dirs = slot_dirs_for_tokens(
                tokens, deliverables_by_id,
            )
        except Exception:  # noqa: BLE001
            slot_dirs = []
        if slot_dirs:
            uniq = list(dict.fromkeys(slot_dirs))
            prompt_parts.append(
                "【交付槽目录·必写】请将对应产物写入本任务工作区内下列相对路径"
                "（目录内文件名可中可英，但必须落在该目录下）：\n- "
                + "\n- ".join(uniq)
            )
            if any("source_code" in d.replace("\\", "/") for d in uniq):
                prompt_parts.append(
                    "【源码硬约束】「项目源码」必须以真实代码文件交付"
                    "（.py/.ts/.js/.java 等），写在 backend/、src/ 或 "
                    "deliverables/source_code/ 下；"
                    "禁止仅用 .md 说明文档冒充源码已完成。"
                )
        # 令牌硬清单：避免只埋头写工程却漏 readme / 扩展名
        try:
            raw_ref2 = getattr(wp, "playbook_ref", None)
            ref2 = json.loads(raw_ref2) if isinstance(raw_ref2, str) else (
                raw_ref2 if isinstance(raw_ref2, dict) else {}
            )
            tokens = list((ref2 or {}).get("obligation_tokens") or [])
            self._obligation_tokens = tokens
            from project_runtime.worker.obligation_feedback import (
                describe_obligation_tokens,
                workspace_tree,
            )

            uniq_lines = describe_obligation_tokens(tokens)
            if uniq_lines:
                prompt_parts.append(
                    "【义务令牌·收工必过】本任务目录必须同时满足：\n- "
                    + "\n- ".join(uniq_lines)
                )
            ws_now = self._task_ws_root()
            wr = getattr(wp, "workspace_root", None)
            if wr:
                ws_now = Path(wr)
            if ws_now is not None:
                prompt_parts.append(
                    "【当前工作区】相对路径目录树：\n"
                    + workspace_tree(ws_now)
                )
            prompt_parts.append(
                "【写入规则】本任务工作区可自由新建/覆盖文件，没有写入配额；"
                "其他任务目录只读。请用 write_file / append_file，"
                "不要调用已拆分的 file_rw。"
            )
            prompt_parts.append(
                "【开工建议】可先输出将要写入的相对路径 JSON 数组，"
                "例如 [\"README.md\", \"src/main/java/App.java\"]；"
                "不覆盖义务令牌时系统会提醒补计划，不强制。"
            )
        except Exception:  # noqa: BLE001
            pass
        prompt_parts.append("\n" + _WORKER_TASK_TRAILER)

        task_id = getattr(wp, "task_id", None)

        # 20号规范 §7 会话记忆接线: Worker 消费 AgentInstance.session_ref
        # （SessionStore 历史经组装器进 messages——三层记忆包裹既有 prompt;
        #  组装 stats 回写压力条数据源）。best-effort: 无实例/异常照常执行。
        session_store = None
        assembler = None
        context_stats: dict = {}
        try:
            from sqlalchemy import select as _select

            from project_runtime.worker.agent_models import (
                AgentInstance,
                AgentStatus,
            )
            from project_runtime.worker.context import (
                ContextAssembler,
                SessionStore,
            )

            inst = (
                await self._session.execute(
                    _select(AgentInstance).where(
                        AgentInstance.current_task_id == task_id,
                        AgentInstance.status == AgentStatus.BUSY,
                    )
                )
            ).scalar_one_or_none()
            if inst is not None:
                session_store = SessionStore.from_json(inst.session_ref)
                self._sanitize_rework_history(session_store, task_id, wp)
                assembler = ContextAssembler(model)
                # 返修单 B: 实时思维链事件的 Agent 归属（AgentPanel 过滤键）
                try:
                    from project_runtime.worker.agent_models import (
                        AgentDefinition,
                    )

                    _d = await self._session.get(
                        AgentDefinition, inst.definition_id
                    )
                    self._current_agent = {
                        "instance_id": inst.id,
                        "name": _d.name if _d is not None else None,
                    }
                except Exception:  # noqa: BLE001 — 归属是显示增强
                    self._current_agent = {
                        "instance_id": inst.id, "name": None,
                    }
        except Exception:  # noqa: BLE001 — 会话记忆是增强不是依赖
            session_store = None
            assembler = None

        if session_store is not None and assembler is not None:
            try:
                current_payload = [
                    {"role": "user", "content": "\n".join(prompt_parts)}
                ]
                messages, context_stats = await assembler.assemble(
                    session_store,
                    role_prompt=_WORKER_ROLE_PROMPT,
                    current_task=None,
                    current_payload=current_payload,
                )
            except Exception:  # noqa: BLE001 — 组装失败退裸 messages
                context_stats = {}
                messages = [
                    {"role": "user", "content": "\n".join(prompt_parts)}
                ]
        else:
            messages = [
                {"role": "user", "content": "\n".join(prompt_parts)}
            ]

        # 模型驱动工具循环（规范 19 §4）: web_search 作为工具暴露，
        # 模型自主决定调用；上限 4 轮；降级矩阵见 §4.4。
        # V1.4: Agent 白名单交集（wp 透传——None=不限）
        tool_wl = getattr(wp, "tool_whitelist", None)
        tools = self._search_tools(whitelist=tool_wl)
        self._tool_adapter = None
        if tools:
            from project_runtime.llm import require_tools_compat
            from project_runtime.llm.tool_name_adapter import ToolNameAdapter

            _compat = require_tools_compat(cfg)
            self._tool_adapter = ToolNameAdapter(
                style=_compat.tool_name_style,
            )
            tools = self._tool_adapter.adapt_tools(tools)
        content = ""
        # V1.4h: 工具历史（强收时拼摘要——防"垃圾成功"空话产物）
        tool_history: list[tuple[str, bool, int]] = []
        # 返修单 A 层②: 已产出内容救援槽（断流续写的喂回源）
        _rescue_parts: dict[str, list[str]] = {
            "content": [], "reasoning": [],
        }
        for round_no in range(1, _TOOL_ROUNDS + 1):
            try:
                # 第二次断流升级续写（已产出内容喂回 messages）
                _sf_key = f"_stream_fails_{wp.task_id}"
                _stream_failures = getattr(self, _sf_key, 0)
                result = None
                for _attempt in range(1, 4):
                    try:
                        result = await self._stream_round(
                            cfg, model, messages, tools
                        )
                        if not result.get("ok"):
                            # 网关返回错误体（非断流）——不重试直接走原降级逻辑
                            break
                        break
                    except Exception as _se:  # noqa: BLE001 — 断流判定
                        if not self._is_stream_failure(_se) or _attempt == 3:
                            raise
                        _stream_failures += 1
                        setattr(self, _sf_key, _stream_failures)
                        logger.warning(
                            "stream broken (attempt %d, task %d): %s "
                            "— reconnecting in %ds",
                            _attempt, wp.task_id, type(_se).__name__,
                            5 * _attempt,
                        )
                        await asyncio.sleep(5 * _attempt)
                        if _stream_failures >= 2:
                            # 层②: 已产出内容喂回——续写而非重来
                            _partial = "".join(
                                _rescue_parts.get("content", [])
                            ) if _rescue_parts else ""
                            if _partial.strip():
                                messages = messages + [
                                    {"role": "assistant", "content": _partial},
                                    {"role": "user", "content": (
                                        "网络中断。请从你刚才停止的地方继续"
                                        "输出，不要重复已输出内容，直接续写。"
                                    )},
                                ]
                                logger.info(
                                    "resume-with-partial injected (task %d, "
                                    "%d chars)", wp.task_id, len(_partial),
                                )
                if result is None:
                    raise RuntimeError("LLM stream retry exhausted")
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "LLM failed"))
                stream = result["response"]
            except Exception as e:  # noqa: BLE001
                # 已拍板：禁止因非法函数名等再 fallback single（剥 tools 假跑）
                if tools and round_no == 1:
                    from project_runtime.llm.compat import (
                        is_invalid_function_name_error,
                    )

                    if is_invalid_function_name_error(e):
                        raise RuntimeError(
                            "网关工具名与本地 compat 不一致，"
                            "请到设置重新测试连通"
                        ) from e
                    raise RuntimeError(
                        "工具调用请求失败（已禁止静默去工具降级）："
                        f"{e}"
                    ) from e
                raise

            reasoning_parts: list[str] = []
            content_parts: list[str] = []
            tool_calls: list[dict] = []
            think_buf = ""
            answer_buf = ""
            saw_chunk = False
            from project_runtime.event.activity import emit_activity

            await emit_activity(
                getattr(self, "_current_project_id", None),
                "等待模型输出…",
                task_id=task_id,
                phase="llm_wait",
                agent=getattr(self, "_current_agent", None),
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                rc = getattr(delta, "reasoning_content", None) or ""
                cc = delta.content or ""
                tcs = getattr(delta, "tool_calls", None) or []
                if rc or cc or tcs:
                    saw_chunk = True
                if rc:
                    reasoning_parts.append(rc)
                    think_buf += rc
                    if len(think_buf) >= 120 or "\n" in think_buf:
                        await _emit_cot(
                            task_id, "thinking", think_buf,
                            agent=getattr(self, "_current_agent", None),
                            project_id=getattr(
                                self, "_current_project_id", None
                            ),
                        )
                        think_buf = ""
                if cc:
                    content_parts.append(cc)
                    _rescue_parts["content"].append(cc)
                    answer_buf += cc
                    if len(answer_buf) >= 80 or "\n" in answer_buf:
                        await _emit_cot(
                            task_id, "answer", answer_buf,
                            agent=getattr(self, "_current_agent", None),
                            project_id=getattr(
                                self, "_current_project_id", None
                            ),
                        )
                        answer_buf = ""
                # 工具调用增量拼装（流式下分片到达）
                for tc in tcs:
                    idx = tc.index or 0
                    while len(tool_calls) <= idx:
                        tool_calls.append(
                            {"id": None, "name": "", "arguments": ""}
                        )
                    if tc.id:
                        tool_calls[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_calls[idx]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls[idx]["arguments"] += tc.function.arguments
            if think_buf:
                await _emit_cot(
                    task_id, "thinking", think_buf,
                    agent=getattr(self, "_current_agent", None),
                    project_id=getattr(self, "_current_project_id", None),
                )
            if answer_buf:
                await _emit_cot(
                    task_id, "answer", answer_buf,
                    agent=getattr(self, "_current_agent", None),
                    project_id=getattr(self, "_current_project_id", None),
                )
            if not saw_chunk:
                await emit_activity(
                    getattr(self, "_current_project_id", None),
                    "模型流结束但无内容，可能网关空响应",
                    task_id=task_id,
                    phase="llm_empty",
                    agent=getattr(self, "_current_agent", None),
                    throttle=False,
                )

            content = "".join(content_parts)
            if not tool_calls:
                # 正常出口: 无工具调用 → 拟终稿
                if not content.strip():
                    content = "".join(reasoning_parts)[-2000:]
                if (
                    not getattr(self, "_plan_list_checked", False)
                    and getattr(self, "_obligation_tokens", None)
                ):
                    from project_runtime.worker.obligation_feedback import (
                        planned_paths_cover,
                    )

                    paths = _extract_planned_paths(content)
                    self._plan_list_checked = True
                    if paths:
                        cover = planned_paths_cover(
                            paths, self._obligation_tokens,
                        )
                        if not cover.ok():
                            if content.strip():
                                messages.append({
                                    "role": "assistant",
                                    "content": content,
                                })
                            messages.append({
                                "role": "user",
                                "content": (
                                    "【计划清单未覆盖令牌——请补路径后再写文件】\n"
                                    + "\n".join(
                                        f"- {p}" for p in cover.problems
                                    )
                                ),
                            })
                            continue
                # V1.6c Claude Stop: 清单 match 硬闸（兼容 legacy tags）
                from project_runtime.event.activity import emit_activity

                await emit_activity(
                    getattr(self, "_current_project_id", None),
                    "交付硬闸校验中…",
                    task_id=task_id,
                    phase="delivery_stop",
                    agent=getattr(self, "_current_agent", None),
                )
                report = await self._delivery_stop_report(wp)
                problems = list(report.problems)
                if problems:
                    tid = int(getattr(wp, "task_id", 0) or 0)
                    used = self._delivery_repair_counts.get(tid, 0)
                    if used >= _DELIVERY_REPAIR_MAX:
                        self._raise_delivery_gap_soft(
                            reason="UNMET_AFTER_EARLY_STOP",
                            report=report,
                            message=(
                                "交付校验未齐（已返工"
                                f"{_DELIVERY_REPAIR_MAX}次仍缺，软放行）："
                                + "；".join(problems)
                            ),
                        )
                    self._delivery_repair_counts[tid] = used + 1
                    if content.strip():
                        messages.append({
                            "role": "assistant", "content": content,
                        })
                    from collections import Counter

                    from project_runtime.worker.obligation_feedback import (
                        repair_nudge,
                    )

                    stats = dict(Counter(
                        str(x.get("error_code") or "UNKNOWN")
                        for x in self._tool_failures
                        if x.get("error_code")
                    ))
                    messages.append({
                        "role": "user",
                        "content": repair_nudge(
                            used, problems, error_stats=stats,
                        ),
                    })
                    logger.info(
                        "delivery stop-gate repair %d/%d task=%s: %s",
                        used + 1, _DELIVERY_REPAIR_MAX, tid, problems,
                    )
                    continue
                # H4: 存在性通过后再做内容完整性（假 pptx/png 等）
                integrity_action = self._integrity_stop_action(report)
                if integrity_action is not None:
                    kind, payload = integrity_action
                    if kind == "soft":
                        self._raise_delivery_gap_soft(**payload)
                    tid = int(getattr(wp, "task_id", 0) or 0)
                    if content.strip():
                        messages.append({
                            "role": "assistant", "content": content,
                        })
                    messages.append({"role": "user", "content": payload})
                    logger.info(
                        "delivery integrity repair task=%s", tid,
                    )
                    continue
                return await self._archive_session(
                    task_id, wp, session_store, content, context_stats
                )

            # 有工具调用 → 执行搜索、喂回、下一轮
            assistant_msg: dict = {
                "role": "assistant", "content": content or None,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"] or "{}",
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
            messages.append(assistant_msg)
            # 返修单 A 层③: 工具轮边界周期落盘（进程级韧性——
            # 断流/重启后 session_ref 可恢复本轮对话历史）
            if session_store is not None:
                try:
                    from project_runtime.worker.context import TaskRecord

                    _inflight = TaskRecord(
                        task_id=wp.task_id,
                        task_name=wp.task_name,
                        messages=list(messages),
                        summary_card=(
                            f"进行中 r{round_no}: "
                            + (content[:200] if content else "工具循环中")
                        ),
                    )
                    session_store.tasks = [
                        t for t in session_store.tasks
                        if t.task_id != wp.task_id
                    ] + [_inflight]
                    from sqlalchemy import update as _upd

                    from project_runtime.worker.agent_models import (
                        AgentInstance as _AgentInst,
                    )

                    await self._session.execute(
                        _upd(_AgentInst)
                        .where(_AgentInst.current_task_id == wp.task_id)
                        .values(session_ref=session_store.to_json())
                    )
                    await self._session.commit()
                except Exception:  # noqa: BLE001 — 落盘是增强
                    logger.debug("inflight session checkpoint failed",
                                 exc_info=True)
            for i, tc in enumerate(tool_calls):
                wire_name = tc["name"]
                internal_name = (
                    self._tool_adapter.to_canonical(wire_name)
                    if self._tool_adapter is not None
                    else wire_name
                )
                result_text, ok = await self._run_search_tool(
                    task_id, internal_name, tc["arguments"], round_no,
                    whitelist=tool_wl,
                )
                tool_history.append((
                    internal_name, ok, len(result_text)
                ))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{i}",
                    "content": result_text,
                })
            await self._maybe_inject_tool_diagnosis(messages, task_id)
            # 继续下一轮（模型基于工具结果继续推理/产出）

        # 预算用尽：Continue nudge → Grace（禁工具）→ 必跑 Stop（不过=软放行）
        return await self._budget_grace_close(
            wp, task_id, messages, cfg, model, content, tool_history,
            session_store, context_stats,
        )

    async def _budget_grace_close(
        self,
        wp,
        task_id: int | None,
        messages: list,
        cfg,
        model: str,
        content: str,
        tool_history: list[tuple[str, bool, int]],
        session_store,
        context_stats: dict,
    ) -> str:
        """工具预算用尽：Continue nudge → Grace（无 tools）→ Stop。

        Grace 后 Stop 不过 → DeliveryGapSoftAcceptError（软放行，不再给工具返工）。
        """
        messages.append({
            "role": "user",
            "content": _BUDGET_GRACE_NUDGE,
        })
        grace_text = ""
        try:
            result = await self._stream_round(cfg, model, messages, tools=None)
            if result.get("ok"):
                stream = result["response"]
                parts: list[str] = []
                reasoning: list[str] = []
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue
                    cc = delta.content or ""
                    rc = getattr(delta, "reasoning_content", None) or ""
                    if cc:
                        parts.append(cc)
                    if rc:
                        reasoning.append(rc)
                grace_text = "".join(parts).strip()
                if not grace_text:
                    grace_text = "".join(reasoning)[-2000:].strip()
        except Exception:  # noqa: BLE001 — Grace 失败仍走 Stop
            logger.warning(
                "budget grace LLM failed task=%s", task_id, exc_info=True,
            )

        if not grace_text:
            if tool_history:
                ok_n = sum(1 for _, ok, _ in tool_history if ok)
                names = sorted({n for n, _, _ in tool_history})
                names_str = "、".join(names[:8])
                grace_text = (
                    f"（工具循环达上限——已执行 {len(tool_history)} 次"
                    f"工具调用（{ok_n} 次成功），涉及工具: {names_str}。"
                    "未能收敛出最终交付内容。）"
                )
            else:
                grace_text = "（工具循环达上限，未能产出最终内容）"

        content = grace_text
        await self._emit_tool_budget_grace(
            task_id, grace_text, tool_history,
        )

        report = await self._delivery_stop_report(wp)
        if report.problems:
            self._raise_delivery_gap_soft(
                reason="DELIVERY_GAP_AFTER_BUDGET",
                report=report,
                message=(
                    "工具轮次用尽后校验未齐（软放行）："
                    + "；".join(report.problems)
                ),
                tool_rounds=_TOOL_ROUNDS,
            )
        # 预算用尽：完整性失败不再返工，直接静默软放行
        integrity_action = self._integrity_stop_action(
            report, allow_repair=False,
        )
        if integrity_action is not None:
            kind, payload = integrity_action
            if kind == "soft":
                self._raise_delivery_gap_soft(
                    **payload, tool_rounds=_TOOL_ROUNDS,
                )
        return await self._archive_session(
            task_id, wp, session_store, content, context_stats,
        )

    async def _emit_tool_budget_grace(
        self,
        task_id: int | None,
        grace_text: str,
        tool_history: list[tuple[str, bool, int]],
    ) -> None:
        """发出 TOOL_BUDGET_GRACE（Dashboard 须可见）。"""
        try:
            from project_runtime.event import EventEngine, RuntimeEventType

            ok_n = sum(1 for _, ok, _ in tool_history if ok)
            names = sorted({n for n, _, _ in tool_history})
            preview = (grace_text or "")[:500]
            await EventEngine().record(
                RuntimeEventType.TOOL_BUDGET_GRACE,
                project_id=getattr(self, "_current_project_id", None),
                task_id=int(task_id or 0) or None,
                phase="WORKER",
                message=(
                    f"工具预算用尽·Grace（{_TOOL_ROUNDS} 轮）"
                    f"：{preview[:120]}"
                ),
                payload={
                    "tool_rounds": _TOOL_ROUNDS,
                    "grace_text": preview,
                    "tool_calls": len(tool_history),
                    "tool_ok": ok_n,
                    "tools": names[:12],
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("emit TOOL_BUDGET_GRACE failed", exc_info=True)

    async def _archive_session(
        self, task_id: int | None, wp, session_store, content: str,
        context_stats: dict,
    ) -> str:
        """任务终态归档（§7.2）: 会话消息+卡片进 SessionStore →
        序列化回写 AgentInstance.session_ref + 压力条数据回写。

        best-effort: 归档失败不影响返回内容（审计记忆可丢，产物不能丢）。
        """
        if task_id is None or session_store is None:
            return content
        try:
            from sqlalchemy import select as _select

            from project_runtime.worker.agent_models import (
                AgentInstance,
                AgentStatus,
            )

            inst = (
                await self._session.execute(
                    _select(AgentInstance).where(
                        AgentInstance.current_task_id == task_id,
                        AgentInstance.status == AgentStatus.BUSY,
                    )
                )
            ).scalar_one_or_none()
            if inst is None:
                return content
            # 归档本任务会话（user prompt + assistant 终稿）
            session_store.archive_task(
                task_id=task_id,
                task_name=getattr(wp, "task_name", f"task-{task_id}"),
                messages=[
                    {"role": "user", "content": getattr(wp, "contract", None)
                     or getattr(wp, "task_name", "")[:500]},
                    {"role": "assistant", "content": content[:8000]},
                ],
            )
            inst.session_ref = session_store.to_json()
            # 压力条数据源回写（§9——组装器计算副产品落行）
            if context_stats:
                inst.context_tokens = int(
                    context_stats.get("context_tokens", 0)
                )
                inst.context_threshold = int(
                    context_stats.get("threshold", 0)
                )
            await self._session.flush()
        except Exception:  # noqa: BLE001 — 归档 best-effort
            pass
        return content

        # ── 搜索工具（规范 19 §4.2）────────────────────────────────────

    def _sanitize_rework_history(self, store, task_id, wp) -> None:
        """SYS-154: 重跑不注入上一轮失败叙述，只留结构化事实。"""
        if store is None or task_id is None:
            return
        facts = (
            "上一轮已结束。不要沿用「工具有写入配额 / 不能新建文件」的判断——"
            "write_file 可以在本任务工作区自由新建文件。"
        )
        gap_txt = ""
        try:
            from project_runtime.worker.obligation_feedback import (
                describe_obligation_tokens,
            )

            lines = describe_obligation_tokens(self._obligation_tokens)
            if lines:
                gap_txt = "义务清单：\n- " + "\n- ".join(lines)
        except Exception:  # noqa: BLE001
            gap_txt = ""
        for tr in getattr(store, "tasks", []) or []:
            if getattr(tr, "task_id", None) != task_id:
                continue
            tr.messages = []
            card = facts
            if gap_txt:
                card = facts + "\n" + gap_txt
            tr.summary_card = card[:800]

    def _enrich_tool_text(
        self, name: str, args: dict, ok: bool, text: str, result: dict,
    ) -> str:
        """SYS-152/155: 失败记账；写入成功附缺口回执。"""
        if not hasattr(self, "_tool_failures"):
            self._tool_failures = []
        if not hasattr(self, "_obligation_tokens"):
            self._obligation_tokens = []
        bare = name.split(":", 1)[-1]
        if not ok:
            self._tool_failures.append({
                "tool": name,
                "error_code": result.get("error_code") or "",
                "path": str(args.get("path") or "")[:200],
            })
            return text
        if bare not in (
            "write_file", "append_file", "office_pptx", "slide_generator",
            "office_docx", "office_xlsx", "office_pdf", "image_raster",
            "arch_diagram",
        ):
            return text
        try:
            from project_runtime.procedure.token_stop import (
                check_obligation_tokens_detailed,
            )
            from project_runtime.worker.obligation_feedback import (
                format_gap_receipt,
            )

            ws = self._task_ws_root()
            if ws is None or not self._obligation_tokens:
                return text
            report = check_obligation_tokens_detailed(
                ws, self._obligation_tokens,
            )
            return text.rstrip() + "\n" + format_gap_receipt(report)
        except Exception:  # noqa: BLE001
            logger.debug("gap receipt failed", exc_info=True)
            return text

    async def _maybe_inject_tool_diagnosis(
        self, messages: list[dict], task_id: int | None,
    ) -> None:
        """SYS-155: 连续失败 ≥3 注入诊断，整轮最多 2 次。"""
        if not hasattr(self, "_diagnosis_injected"):
            self._diagnosis_injected = 0
        if not hasattr(self, "_tool_failures"):
            self._tool_failures = []
        if self._diagnosis_injected >= 2:
            return
        from project_runtime.worker.obligation_feedback import (
            tool_failure_diagnosis,
        )

        msg = tool_failure_diagnosis(self._tool_failures)
        if not msg:
            return
        # 只对「尚未诊断过的」尾部失败生效：注入后清空计数，避免每轮刷屏
        self._tool_failures = []
        self._diagnosis_injected += 1
        messages.append({"role": "user", "content": msg})
        try:
            from project_runtime.event import EventEngine, RuntimeEventType

            await EventEngine().record(
                RuntimeEventType.TOOL_FAILURE_DIAGNOSIS,
                task_id=task_id,
                phase="LLM_TOOL",
                message=msg[:300],
                actor="WORKER",
                payload={"round_injected": self._diagnosis_injected},
            )
        except Exception:  # noqa: BLE001
            pass

    def _search_tools(
        self, whitelist: list[str] | None = None
    ) -> list[dict] | None:
        """工具循环的工具面: web_search + 全部 TOOL 插件（描述即调用策略）。

        20号规范 §4.3 三能力①: 插件自主调用——白名单内全部插件以
        tool 形式进循环，模型按任务上下文自决调不调/怎么调。
        可见性 = 白名单 ∩ 已注册（返修-20号-V1.4）:
        whitelist=None → 全部已注册 TOOL 插件（老路径/不限模板）;
        whitelist=list → 仅列内插件（裸名比对——剥 tool: 前缀）。
        web_search 同受白名单门控（三能力③配额内基线必含）。
        RESEARCH 无 provider → 无 web_search; TOOL 面零注册 →
        仅 web_search（若在）。全空 → None。
        """
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.worker.tools import expand_tool_whitelist

        registry = shared_registry()
        providers = registry.list_providers(CapabilityType.RESEARCH)
        search_ok = any(i.enabled for i in providers)

        # TOOL 插件面（V1.4: 白名单 ∩ 已注册——裸名比对剥前缀）
        allowed = (
            None if whitelist is None
            else set(expand_tool_whitelist(whitelist))
        )
        tool_defs: list[dict] = []
        for info in registry.list_providers(CapabilityType.TOOL):
            if allowed is not None:
                bare = info.name.split(":", 1)[-1]
                if bare not in allowed and info.name not in allowed:
                    continue  # 白名单外——模型不可见（护栏）
            if not info.enabled:
                continue
            schema_fn = getattr(info.provider, "tool_schema", None)
            if not callable(schema_fn):
                continue  # 无 schema 契约的 provider 不进循环
            schema = schema_fn()
            if not isinstance(schema, dict) or not schema.get("name"):
                continue
            tool_defs.append({
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": (
                        schema.get("description") or schema["name"]
                    )[:600],
                    "parameters": schema.get("parameters") or {
                        "type": "object", "properties": {},
                    },
                },
            })
        if not search_ok and not tool_defs:
            return None  # 可用性决定可见性（opencode 同款）
        # web_search 门控（三能力③: web_search 是每模板基线——
        # 但白名单显式列出且不含它时同样不可见，护栏优先）
        if allowed is not None and "web_search" not in allowed:
            search_ok = False
        if not search_ok:
            return tool_defs

        web_search_def = [{
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索互联网获取当前任务所需信息。仅当任务缺少必要的外部"
                    "知识时调用：特定技术/框架的 API 用法与版本兼容性、行业"
                    "最佳实践、明确的客观事实查证。任务材料（上下文/契约/"
                    "方法指导）已自足时不要调用。每次调用消耗搜索配额，能从"
                    "已有材料推断的优先推断。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询（自然语言，中文或英文）",
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "结果数（默认 6，最大 10）",
                        },
                    },
                    "required": ["query"],
                },
            },
        }]
        return web_search_def + tool_defs

    async def _run_tool_plugin(
        self, name: str, args: dict,
        whitelist: list[str] | None = None,
    ) -> dict | None:
        """TOOL 插件分发（§4.3 三能力①）。

        循环里暴露的 tool:xxx / mcp_xxx 在 shared_registry 找
        provider → invoke；未注册返回 None（unknown tool 语义）。
        V1.4 纵深防御: whitelist 内不含的工具名直接拒（模型幻觉出
        未暴露但已注册的工具也拒——返回降级文案而非执行）。
        """
        from project_runtime.worker.tools import (
            RENAMED_TOOLS,
            expand_tool_whitelist,
            renamed_tool_error,
        )

        if name in RENAMED_TOOLS:
            return renamed_tool_error(name)
        if whitelist is not None:
            allowed = set(expand_tool_whitelist(whitelist))
            bare = name.split(":", 1)[-1]
            if bare not in allowed and name not in allowed:
                return {
                    "ok": False,
                    "error": (
                        f"工具 {name} 不在本 Agent 白名单内——"
                        "该能力对当前角色不可用，请用白名单内工具完成。"
                    ),
                }
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType

        for info in shared_registry().list_providers(CapabilityType.TOOL):
            if info.name == name and info.enabled:
                try:
                    payload = dict(args)
                    # V1.6 序0: 文件工具写禁闭——注入任务工作区根
                    # （__ 前缀=内部参数, schema 不暴露, Provider 侧剥离）
                    if name in (
                        "tool:read_file", "tool:list_dir",
                        "tool:write_file", "tool:append_file",
                        "tool:office_pptx", "tool:office_docx",
                        "tool:office_xlsx", "tool:office_pdf",
                        "tool:image_raster", "tool:office_inspect",
                        "tool:arch_diagram",
                    ) and self._workspace_root:
                        payload["__ws_root"] = self._workspace_root
                    return await info.provider.invoke(payload)
                except Exception as e:  # noqa: BLE001 — 降级不炸循环
                    return {"ok": False, "error": str(e)[:300]}
        return None

    async def _emit_tool_result(
        self, task_id: int | None, name: str, ok: bool, text: str,
        t0: float, round_no: int, result: dict | None = None,
    ) -> None:
        """工具结果审计事件（全文不进事件——正文归属 TaskRun）。"""
        import time as _time

        from project_runtime.event import EventEngine, RuntimeEventType

        payload = {
            "tool": name,
            "ok": ok,
            "chars": len(text),
            "duration_ms": int((_time.monotonic() - t0) * 1000),
            "round": round_no,
        }
        if not ok and result:
            payload["error_code"] = result.get("error_code")
            payload["error"] = str(result.get("error") or "")[:300]
        short = name.removeprefix("tool:")
        text = (
            f"⚙ 工具完成 {short} "
            f"{'成功' if ok else '失败'}"
            f"（{payload['duration_ms']}ms）"
        )
        if payload.get("error_code"):
            text += f" [{payload['error_code']}]"
        payload["text"] = text
        try:
            await EventEngine().record(
                RuntimeEventType.LLM_TOOL_RESULT,
                project_id=getattr(self, "_current_project_id", None),
                task_id=task_id,
                phase="LLM_TOOL",
                message=text[:120],
                actor="WORKER",
                payload=payload,
            )
        except Exception:  # noqa: BLE001
            pass

    async def _emit_tool_call(
        self, task_id: int | None, name: str, args: dict, round_no: int,
        *, repaired: bool = False, raw_chars: int | None = None,
    ) -> None:
        """工具调用审计事件：只记参数要点与长度，不记正文。"""
        from project_runtime.event import EventEngine, RuntimeEventType

        short = name.removeprefix("tool:")
        if name == "web_search" or short == "web_search":
            query = str(args.get("query", ""))[:300]
            text = f"⚙ 调用工具 web_search：{query[:80]}"
            payload: dict = {
                "tool": name, "query": query, "round": round_no, "text": text,
            }
        else:
            path = str(args.get("path") or args.get("filename") or "")[:300]
            kind = str(
                args.get("kind") or args.get("action")
                or args.get("runner") or ""
            )[:50]
            body = args.get("content", args.get("markdown"))
            text = f"⚙ 调用工具 {short}"
            if path:
                text += f"：{path}"
            elif kind:
                text += f"：{kind}"
            payload = {
                "tool": name,
                "round": round_no,
                "path": path,
                "kind": kind,
                "content_chars": None if body is None else len(str(body)),
                "arg_keys": sorted(k for k in args if not k.startswith("__")),
                "repaired": repaired,
                "text": text,
            }
            if raw_chars is not None:
                payload["raw_chars"] = raw_chars
        try:
            await EventEngine().record(
                RuntimeEventType.LLM_TOOL_CALL,
                project_id=getattr(self, "_current_project_id", None),
                task_id=task_id,
                phase="LLM_TOOL",
                message=text[:120],
                actor="LLM",
                payload=payload,
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _missing_required(name: str, args: dict) -> list[str]:
        """按已注册 TOOL 的 schema.required 校验参数（未注册/无 schema → []）。"""
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType

        for info in shared_registry().list_providers(CapabilityType.TOOL):
            if info.name != name:
                continue
            schema_fn = getattr(info.provider, "tool_schema", None)
            if not callable(schema_fn):
                return []
            try:
                params = (schema_fn() or {}).get("parameters") or {}
            except Exception:  # noqa: BLE001
                return []
            missing = []
            for key in params.get("required") or []:
                v = args.get(key)
                if v is None or (isinstance(v, str) and not v.strip()):
                    missing.append(key)
            return missing
        return []

    async def _run_search_tool(
        self, task_id: int | None, name: str, arguments: str, round_no: int,
        whitelist: list[str] | None = None,
    ) -> tuple[str, bool]:
        """执行模型发起的搜索工具 → (结果文本, ok)。审计双事件。"""
        import json as _json
        import time as _time

        from project_runtime.event import EventEngine, RuntimeEventType
        from project_runtime.worker.tools import tool_error

        t0 = _time.monotonic()
        was_repaired = False
        try:
            args = _json.loads(arguments or "{}")
        except ValueError:
            # V1.4i 防线3: 流式拼装断裂——先尝试尾部修复（防线1）
            repaired = _repair_arguments(arguments)
            if repaired is not None:
                try:
                    args = _json.loads(repaired)
                except ValueError:
                    repaired = None
            if repaired is None:
                # 无法修复——返回可理解错误（模型可自救: 重试/拆短）
                fail = tool_error(
                    "TRUNCATED",
                    "工具调用参数传输不完整（长内容在流式传输中被截断）",
                    "请重试——把长内容拆成多个较短的文件分别写入",
                )
                text = _tool_result_text(fail)
                await self._emit_tool_call(
                    task_id, name, {}, round_no,
                    repaired=True, raw_chars=len(arguments or ""),
                )
                await self._emit_tool_result(
                    task_id, name, False, text, t0, round_no, result=fail,
                )
                return text, False
            was_repaired = True
        if not isinstance(args, dict):
            args = {}
        query = str(args.get("query", ""))[:300]
        await self._emit_tool_call(
            task_id, name, args, round_no, repaired=was_repaired,
            raw_chars=len(arguments or "") if was_repaired else None,
        )

        results: list[dict] = []
        err = ""
        if name == "web_search" and query:
            try:
                results = await self._search_via_registry(query, args)
            except Exception as e:  # noqa: BLE001
                err = str(e)[:200]
        elif not name.startswith("web_search"):
            # TOOL 插件分发（§4.3 三能力①——注册进循环的插件在此执行）
            missing = self._missing_required(name, args)
            if missing:
                # 截断修复出的 JSON 合法但可能丢字段——不得带半截参数执行
                tool_result = tool_error(
                    "TRUNCATED" if was_repaired else "MISSING_FIELD",
                    f"{name} 缺少必填参数: {', '.join(missing)}"
                    + ("（参数在传输中被截断）" if was_repaired else ""),
                    "请把长内容拆成多个较短的文件分别写入后重试"
                    if was_repaired else
                    f"请补齐 {', '.join(missing)} 后重新调用",
                )
            else:
                if was_repaired:
                    args = {**args, "__repaired": True}
                tool_result = await self._run_tool_plugin(
                    name, args, whitelist=whitelist
                )
            if tool_result is not None:
                ok = bool(tool_result.get("ok"))
                text = _tool_result_text(tool_result)
                text = self._enrich_tool_text(
                    name, args, ok, text, tool_result,
                )
                await self._emit_tool_result(
                    task_id, name, ok, text, t0, round_no, result=tool_result,
                )
                return text, ok
            err = f"unknown tool: {name}"
        else:
            err = "web_search: query 必填"

        ok = bool(results) and not err
        if results:
            lines = []
            for r in results:
                lines.append(
                    f"标题: {r.get('title', '')}\n"
                    f"来源: {r.get('source', '')}\n"
                    f"摘要: {r.get('content', '')}"
                )
            text = "\n\n".join(lines)[:6000]
        else:
            text = (
                f"搜索失败或无结果{': ' + err if err else ''}"
                "——请基于已有材料完成任务。"
            )

        # 审计: 结果事件（全文不进事件——正文归属 TaskRun/研究记录）
        try:
            await EventEngine().record(
                RuntimeEventType.LLM_TOOL_RESULT,
                task_id=task_id,
                phase="LLM_TOOL",
                message=f"web_search ok={ok} ({len(results)} 条)",
                actor="WORKER",
                payload={
                    "tool": name,
                    "ok": ok,
                    "result_count": len(results),
                    "chars": len(text),
                    "duration_ms": int((_time.monotonic() - t0) * 1000),
                    "round": round_no,
                },
            )
        except Exception:  # noqa: BLE001
            pass
        return text, ok

    async def _search_via_registry(
        self, query: str, args: dict
    ) -> list[dict]:
        """经共享 registry 执行搜索（Exa 优先 → scrape 兜底）。"""
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.research.providers import (
            register_search_providers,
        )

        registry = shared_registry()
        register_search_providers(registry)
        # 20号规范 §4.3 三能力①: TOOL 插件面注册（幂等——批次1+3 十件）
        try:
            from project_runtime.worker.tools import register_worker_tools

            register_worker_tools(registry)
            from project_runtime.worker.coding_tools import (
                register_batch3_tools,
            )

            register_batch3_tools(registry)
        except Exception:  # noqa: BLE001 — 插件面失败不阻断搜索
            pass
        n = min(int(args.get("num_results") or 6), 10)
        for info in registry.list_providers(CapabilityType.RESEARCH):
            if not info.enabled:
                continue
            try:
                results = await info.provider.invoke(
                    {"query": query, "num_results": n}
                )
                if results:
                    return results
            except Exception:  # noqa: BLE001 — provider 间兜底
                logger.debug(
                    "search provider %s failed", info.name, exc_info=True
                )
        return []


async def _emit_cot(
    task_id: int | None, phase: str, text: str,
    agent: dict | None = None,
    project_id: int | None = None,
) -> None:
    """思维链/答案增量 → 事件（best-effort，绝不打断执行）。

    返修单 B: agent 归属（instance_id/name）进 payload——
    AgentPanel 按 agent_instance_id 过滤出每个智能体的实时思维链。
    project_id 应由调用方缓存传入；缺省才回查一次（避免每 chunk 开事务）。
    """
    try:
        from project_runtime.event import EventEngine, RuntimeEventType

        evt = (
            RuntimeEventType.LLM_THINKING_CHUNK
            if phase == "thinking"
            else RuntimeEventType.LLM_ANSWER_CHUNK
        )
        payload = {"text": text}
        if agent:
            payload["agent_instance_id"] = agent.get("instance_id")
            if agent.get("name"):
                payload["agent_name"] = agent["name"]
        if project_id is None and task_id is not None:
            try:
                from sqlalchemy import select as _sel

                from infrastructure.database import engine as _eng
                from project_runtime.project_state.models import Task as _Tk

                async with AsyncSession(_eng) as _s:
                    project_id = (
                        await _s.execute(
                            _sel(_Tk.project_id).where(_Tk.id == task_id)
                        )
                    ).scalar()
            except Exception:  # noqa: BLE001 — 归属 best-effort
                project_id = None
        await EventEngine().record(
            evt,
            project_id=project_id,
            task_id=task_id,
            phase=f"LLM_{phase.upper()}",
            message=text[-128:],
            actor=(
                agent.get("name") or "LLM"
            ) if agent else "LLM",
            payload=payload,
        )
    except Exception:  # noqa: BLE001 — 显示链路绝不影响执行
        pass
