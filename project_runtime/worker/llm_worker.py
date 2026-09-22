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


class LLMWorkerAdapter(WorkerAdapter):
    """Real worker: executes tasks via LLM (LiteLLM)."""

    def __init__(
        self,
        session: AsyncSession,
        artifact_mgr: "ArtifactManager | None" = None,
        max_tokens: int = 4000,
    ):
        self._session = session
        self._fsm = StateTransitionService(session)
        self._mgr = TaskGraphManager(session)
        self._artifact_mgr = artifact_mgr
        self._max_tokens = max_tokens
        self._db_lock = asyncio.Lock()

    async def assign(self, wp: WorkPackage) -> ExecutionResult:
        # Phase 1 (DB, locked): validate READY + RUNNING transition
        async with self._db_lock:
            task = await self._mgr.get_task(wp.task_id)
            if task is None:
                raise ValueError(f"Task not found: {wp.task_id}")
            if task.status != TaskStatus.READY:
                raise ValueError(
                    f"Worker cannot start task in status {task.status.value}. Must be READY."
                )
            await self._fsm.transition_task(task, TaskStatus.RUNNING)
            await self._session.flush()

        # Execute via LLM (outside DB lock — network call)
        try:
            output = await self._execute_llm(wp)
            success = True
        except Exception as e:
            logger.error("LLM execution failed for task %d: %s", wp.task_id, e)
            output = f"LLM execution failed: {e}"
            success = False

        # Phase 2 (DB, locked): EXECUTION_DONE + TaskRun + Artifact
        async with self._db_lock:
            await self._session.refresh(task)
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
                artifact_id = artifact.id

            await self._session.flush()

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
        import litellm

        from project_runtime.llm import load_llm_settings

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            raise RuntimeError("LLM not configured")

        model = cfg.model
        known = ("openai/", "azure/", "anthropic/", "huggingface/", "ollama/",
                 "gemini/", "deepseek/", "mistral/")
        if "/" in model and not model.startswith(known):
            model = f"openai/{model}"

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

        resp = await litellm.acompletion(
            model=model,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": "\n".join(prompt_parts)}],
            max_tokens=self._max_tokens,
            timeout=120,
        )
        return resp.choices[0].message.content or ""
