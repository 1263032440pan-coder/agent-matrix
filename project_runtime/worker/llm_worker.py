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
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.adapter import ExecutionResult, WorkerAdapter, WorkPackage

if TYPE_CHECKING:
    from project_runtime.artifact.manager import ArtifactManager

logger = logging.getLogger(__name__)

_TOOL_ROUNDS = 4  # 工具循环上限（规范 19 §4.5）


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
        self._db_lock = asyncio.Lock()

    async def assign(self, wp: WorkPackage) -> ExecutionResult:
        # Phase 1 (short tx): validate READY + RUNNING transition, commit
        # immediately — the LLM call must not hold a write transaction (WP-041
        # §3 / DB-04). Previously flush()-only kept the tx open across the
        # network call.
        from infrastructure.database.short_tx import commit_with_retry

        async with self._db_lock:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")
            if task.status != TaskStatus.READY:
                raise ValueError(
                    f"Worker cannot start task in status {task.status.value}. Must be READY."
                )
            await self._fsm.transition_task(task, TaskStatus.RUNNING)
            await commit_with_retry(self._session, context="llm_worker.phase1")

        # Execute via LLM (zero DB transaction held — network call)
        try:
            output = await self._execute_llm(wp)
            success = True
        except Exception as e:
            logger.error("LLM execution failed for task %d: %s", wp.task_id, e)
            output = f"LLM execution failed: {e}"
            success = False

        # Phase 2 (short tx): re-fetch task (Phase-1 commit expired it),
        # EXECUTION_DONE/FAILED + TaskRun + Artifact, then commit.
        async with self._db_lock:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")
            if task.status == TaskStatus.CANCELLED:
                return ExecutionResult(task_id=wp.task_id, success=False, output="cancelled")

            if success:
                await self._fsm.transition_task(task, TaskStatus.EXECUTION_DONE)
            else:
                await self._fsm.transition_task(task, TaskStatus.FAILED)
            await self._session.flush()

            # TaskRun audit
            from project_runtime.project_state.models import TaskRun

            now = datetime.datetime.now(datetime.timezone.utc)
            run_payload: dict = {"output": output[:2000]}
            if wp.skill_context is not None:
                run_payload["skill"] = {
                    "skill_id": wp.skill_context.skill_id,
                    "skill_version": wp.skill_context.skill_version,
                    "content_hash": wp.skill_context.skill_content_hash,
                }
            run = TaskRun(
                task_id=task.id,
                attempt=1,
                started_at=now,
                finished_at=now,
                result=json.dumps(run_payload, ensure_ascii=False),
            )
            self._session.add(run)

            # Artifact
            artifact_id: int | None = None
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

            await commit_with_retry(self._session, context="llm_worker.phase2")

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

    async def _execute_llm(self, wp: WorkPackage) -> str:
        from project_runtime.llm import (
            load_llm_settings,
            normalize_model_for_gateway,
        )

        # AOP-P2-2 清偿: litellm 直调 → LLM Capability Provider
        from project_runtime.llm.provider import llm_acomplete

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            raise RuntimeError("LLM not configured")

        model = normalize_model_for_gateway(cfg.model, cfg.base_url)

        # Build prompt from task contract + optional skill
        prompt_parts = [f"任务：{wp.task_name}"]
        if wp.playbook_ref:
            prompt_parts.append(f"上下文：{wp.playbook_ref}")
        if wp.contract:
            prompt_parts.append(f"契约：{wp.contract}")
        if wp.skill_context:
            prompt_parts.append(f"方法指导：\n{wp.skill_context.skill_body[:2000]}")
        prompt_parts.append(
            "\n请完成上述任务，输出具体的交付内容（markdown 格式）。"
            "不要输出思考过程，直接给出结果。"
        )

        task_id = getattr(wp, "task_id", None)
        messages: list[dict] = [
            {"role": "user", "content": "\n".join(prompt_parts)}
        ]

        # 模型驱动工具循环（规范 19 §4）: web_search 作为工具暴露，
        # 模型自主决定调用；上限 4 轮；降级矩阵见 §4.4。
        tools = self._search_tools()
        content = ""
        for round_no in range(1, _TOOL_ROUNDS + 1):
            try:
                result = await llm_acomplete(
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
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "LLM failed"))
                stream = result["response"]
            except Exception as e:  # noqa: BLE001
                # 降级: 网关不支持 tools（报错）→ 剔除 tools 重发单轮
                if tools and round_no == 1:
                    logger.info("tools unsupported (%s) — fallback single", e)
                    tools = None
                    result = await llm_acomplete(
                        model=model,
                        api_base=cfg.base_url,
                        api_key=cfg.api_key,
                        messages=messages,
                        max_tokens=self._max_tokens,
                        timeout=600,
                        stream=True,
                    )
                    if not result.get("ok"):
                        raise RuntimeError(
                            result.get("error", "LLM failed")
                        )
                    stream = result["response"]
                else:
                    raise

            reasoning_parts: list[str] = []
            content_parts: list[str] = []
            tool_calls: list[dict] = []
            think_buf = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                rc = getattr(delta, "reasoning_content", None) or ""
                cc = delta.content or ""
                tcs = getattr(delta, "tool_calls", None) or []
                if rc:
                    reasoning_parts.append(rc)
                    think_buf += rc
                    if len(think_buf) >= 120 or "\n" in think_buf:
                        await _emit_cot(task_id, "thinking", think_buf)
                        think_buf = ""
                if cc:
                    content_parts.append(cc)
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
                await _emit_cot(task_id, "thinking", think_buf)

            content = "".join(content_parts)
            if not tool_calls:
                # 正常出口: 无工具调用 → 本轮即终稿
                if not content.strip():
                    content = "".join(reasoning_parts)[-2000:]
                return content

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
            for i, tc in enumerate(tool_calls):
                result_text, ok = await self._run_search_tool(
                    task_id, tc["name"], tc["arguments"], round_no
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"] or f"call_{i}",
                    "content": result_text,
                })
            # 继续下一轮（模型基于搜索结果继续推理/产出）
        # 4 轮用尽强收（防搜索上瘾，规范 19 §4.5）
        if not content.strip():
            content = "（工具循环达上限，未能产出最终内容）"
        return content

    # ── 搜索工具（规范 19 §4.2）────────────────────────────────────

    def _search_tools(self) -> list[dict] | None:
        """web_search 工具定义（描述即调用策略）。registry 无 provider → None。"""
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType

        providers = shared_registry().list_providers(
            CapabilityType.RESEARCH
        )
        if not any(i.enabled for i in providers):
            return None  # 可用性决定可见性（opencode 同款）
        return [{
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

    async def _run_search_tool(
        self, task_id: int | None, name: str, arguments: str, round_no: int
    ) -> tuple[str, bool]:
        """执行模型发起的搜索工具 → (结果文本, ok)。审计双事件。"""
        import json as _json
        import time as _time

        from project_runtime.event import EventEngine, RuntimeEventType

        t0 = _time.monotonic()
        try:
            args = _json.loads(arguments or "{}")
        except ValueError:
            args = {"query": str(arguments)[:200]}
        query = str(args.get("query", ""))[:300]
        # 审计: 调用事件
        try:
            await EventEngine().record(
                RuntimeEventType.LLM_TOOL_CALL,
                task_id=task_id,
                phase="LLM_TOOL",
                message=f"web_search: {query[:100]}",
                actor="LLM",
                payload={"tool": name, "query": query, "round": round_no},
            )
        except Exception:  # noqa: BLE001
            pass

        results: list[dict] = []
        err = ""
        if name == "web_search" and query:
            try:
                results = await self._search_via_registry(query, args)
            except Exception as e:  # noqa: BLE001
                err = str(e)[:200]
        else:
            err = f"unknown tool: {name}"

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


async def _emit_cot(task_id: int | None, phase: str, text: str) -> None:
    """思维链/答案增量 → 事件（best-effort，绝不打断执行）。"""
    try:
        from project_runtime.event import EventEngine, RuntimeEventType

        evt = (
            RuntimeEventType.LLM_THINKING_CHUNK
            if phase == "thinking"
            else RuntimeEventType.LLM_ANSWER_CHUNK
        )
        await EventEngine().record(
            evt,
            task_id=task_id,
            phase=f"LLM_{phase.upper()}",
            message=text[-128:],
            actor="LLM",
            payload={"text": text},
        )
    except Exception:  # noqa: BLE001 — 显示链路绝不影响执行
        pass
