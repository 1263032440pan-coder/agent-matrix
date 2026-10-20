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
                assembler = ContextAssembler(model)
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
                    role_prompt=(
                        "你是项目开发智能体，按任务契约交付。"
                        "历史任务卡片供参考，不要复述。"
                    ),
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
        return await self._archive_session(
            task_id, wp, session_store, content, context_stats
        )

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

    def _search_tools(self) -> list[dict] | None:
        """工具循环的工具面: web_search + 全部 TOOL 插件（描述即调用策略）。

        20号规范 §4.3 三能力①: 插件自主调用——白名单内全部插件以
        tool 形式进循环，模型按任务上下文自决调不调/怎么调。
        可用性决定可见性: RESEARCH 无 provider → 无 web_search;
        TOOL 面零注册 → 仅 web_search（若在）。全空 → None。
        """
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType

        registry = shared_registry()
        providers = registry.list_providers(CapabilityType.RESEARCH)
        search_ok = any(i.enabled for i in providers)

        # TOOL 插件面（注册即暴露——白名单=已注册∩启用）
        tool_defs: list[dict] = []
        for info in registry.list_providers(CapabilityType.TOOL):
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

    async def _run_tool_plugin(self, name: str, args: dict) -> dict | None:
        """TOOL 插件分发（§4.3 三能力①）。

        循环里暴露的 tool:xxx / mcp_xxx 在 shared_registry 找
        provider → invoke；未注册返回 None（unknown tool 语义）。
        """
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType

        for info in shared_registry().list_providers(CapabilityType.TOOL):
            if info.name == name and info.enabled:
                try:
                    return await info.provider.invoke(args)
                except Exception as e:  # noqa: BLE001 — 降级不炸循环
                    return {"ok": False, "error": str(e)[:300]}
        return None

    async def _emit_tool_result(
        self, task_id: int | None, name: str, ok: bool, text: str,
        t0: float, round_no: int,
    ) -> None:
        """工具结果审计事件（全文不进事件——正文归属 TaskRun）。"""
        import time as _time

        from project_runtime.event import EventEngine, RuntimeEventType

        try:
            await EventEngine().record(
                RuntimeEventType.LLM_TOOL_RESULT,
                task_id=task_id,
                phase="LLM_TOOL",
                message=f"{name} ok={ok}",
                actor="WORKER",
                payload={
                    "tool": name,
                    "ok": ok,
                    "chars": len(text),
                    "duration_ms": int((_time.monotonic() - t0) * 1000),
                    "round": round_no,
                },
            )
        except Exception:  # noqa: BLE001
            pass

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
        elif not name.startswith("web_search"):
            # TOOL 插件分发（§4.3 三能力①——注册进循环的插件在此执行）
            tool_result = await self._run_tool_plugin(name, args)
            if tool_result is not None:
                ok = bool(tool_result.get("ok"))
                text = str(tool_result.get("stdout")
                           or tool_result.get("result")
                           or tool_result.get("error")
                           or "")[:6000]
                await self._emit_tool_result(
                    task_id, name, ok, text, t0, round_no
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
