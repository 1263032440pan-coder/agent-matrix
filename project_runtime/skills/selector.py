"""LLM Skill Selector (用户需求: 程序根据任务内容自动选 skill).

方式（用户指定）: skill 只有几十个——把每个 skill 的
  name + description + when_to_use
作为 function/tool 列表给 LLM，让 function calling 自己选。
不遍历全文，只看简短描述。

两个接入点:
  - 生成计划（PlaybookBuilder 方法不足 → 选 PROJECT_METHOD skill）
  - 执行任务（ExecutionRunner Phase B-0 → 选 TASK_EXECUTION skill）

红线延续 (V1.12):
  - AUTO_RESOLUTION_EXCLUDED 状态的 skill 不进候选
  - LLM 不可用 → 关键词退化匹配（name/description/when_to_use 命中计分）
  - 决不编造: LLM 只能从候选里选，返回的 skill_id 必须在候选集内
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.models import SkillDefinition

logger = logging.getLogger(__name__)

_MAX_CANDIDATES = 30  # 几十个 skill — 全量进 tool 列表


class SkillSelector:
    """LLM function-calling 式 skill 选择器。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── 候选集 ─────────────────────────────────────────────────────────
    async def candidates(self, skill_type) -> list[SkillDefinition]:
        from sqlalchemy import select

        from project_runtime.skills.enums import AUTO_RESOLUTION_EXCLUDED

        stmt = (
            select(SkillDefinition)
            .where(SkillDefinition.status.notin_(list(AUTO_RESOLUTION_EXCLUDED)))
            .order_by(SkillDefinition.id)
            .limit(_MAX_CANDIDATES)
        )
        if skill_type is not None:
            stmt = stmt.where(SkillDefinition.skill_type == skill_type)
        return list((await self._session.execute(stmt)).scalars().all())

    # ── LLM 选择 ──────────────────────────────────────────────────────
    async def select(
        self,
        *,
        task_content: str,
        skill_type=None,
        extra_context: str = "",
    ) -> SkillDefinition | None:
        """按任务内容从候选 skill 中选一个最合适的。

        Returns: 选中的 SkillDefinition，无合适候选/LLM 失败 → None
        """
        pool = await self.candidates(skill_type)
        if not pool:
            return None
        if len(pool) == 1:
            return pool[0]

        picked_id = await self._llm_pick(pool, task_content, extra_context)
        if picked_id is None:
            picked_id = self._keyword_pick(pool, task_content)
        if picked_id is None:
            return None
        for sk in pool:
            if sk.skill_id == picked_id:
                return sk
        return None  # LLM 幻觉出的 id — 决不编造，宁可不选

    async def _llm_pick(
        self, pool: list[SkillDefinition], task_content: str, extra: str
    ) -> str | None:
        """LLM function-calling 式选择（tool = skill 简述列表）。"""
        try:
            import asyncio

            import litellm

            from project_runtime.llm import (
                load_llm_settings,
                normalize_model_for_gateway,
            )

            cfg = load_llm_settings()
            if not (cfg.base_url and cfg.api_key and cfg.model):
                return None
            model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)

            tools = []
            for sk in pool:
                desc = (sk.description or "")[:300]
                wtu = (sk.when_to_use or "")[:300]
                tools.append({
                    "type": "function",
                    "function": {
                        "name": f"skill_{sk.skill_id}",
                        "description": (
                            f"name: {sk.name}\\n"
                            f"description: {desc}\\n"
                            f"when_to_use: {wtu}"
                        )[:600],
                        "parameters": {"type": "object", "properties": {}},
                    },
                })

            content = task_content[:4000]
            if extra:
                content = f"{content}\\n\\n[上下文]\\n{extra[:1000]}"

            def _blocking() -> str | None:
                resp = litellm.completion(
                    model=model_id,
                    api_base=cfg.base_url,
                    api_key=cfg.api_key,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"任务内容：\\n{content}\\n\\n"
                            "从下面的 skill 工具列表中选择一个最适合"
                            "辅助完成该任务的 skill（只能选列表中的，"
                            "没有合适的就不调用任何工具）。"
                        ),
                    }],
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=2000,
                    timeout=120,
                )
                msg = resp.choices[0].message
                tcs = getattr(msg, "tool_calls", None) or []
                if tcs:
                    return tcs[0].function.name
                # 某些网关不吐 tool_calls — 文本里找 skill_ 前缀
                text = msg.content or ""
                for sk in pool:
                    if f"skill_{sk.skill_id}" in text:
                        return f"skill_{sk.skill_id}"
                return None

            raw = await asyncio.wait_for(
                asyncio.to_thread(_blocking), timeout=130
            )
            if raw and raw.startswith("skill_"):
                return raw[len("skill_"):]
            return None
        except Exception:  # noqa: BLE001 — LLM 是增强不是依赖
            logger.debug("LLM skill pick failed", exc_info=True)
            return None

    @staticmethod
    def _keyword_pick(
        pool: list[SkillDefinition], task_content: str
    ) -> str | None:
        """LLM 不可用时的退化: 关键词命中计分（阈值 2）。"""
        text = task_content.lower()
        best_id, best_score = None, 0
        for sk in pool:
            # 候选自己的关键词（中文按 2 字滑窗粗切）
            words = set()
            for src in (sk.name or "", sk.when_to_use or "",
                        sk.description or ""):
                src = src.strip()
                if len(src) >= 2:
                    if src.isascii():
                        words.update(src.lower().split())
                    else:
                        words.update(
                            src[i:i + 2] for i in range(len(src) - 1)
                        )
            score = sum(1 for w in words if len(w) >= 2 and w in text)
            if score > best_score:
                best_id, best_score = sk.skill_id, score
        return best_id if best_score >= 2 else None


def skill_brief(sk: SkillDefinition) -> dict:
    """skill 简述（事件/日志用）。"""
    return {
        "skill_id": sk.skill_id,
        "name": sk.name,
        "version": sk.version,
        "skill_type": sk.skill_type.value if hasattr(sk.skill_type, "value")
        else str(sk.skill_type),
    }
