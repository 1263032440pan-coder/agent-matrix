"""Online Skill Fabricator（用户需求: 无可用 skill 时联网制作 skill）.

链路:
  ExecutionRunner 无绑定命中 + LLM 自动选择也无候选
    → WebSearchProvider 联网搜索（复用偏离②③ 的执行体）
    → LLM 综合: 搜索结果 → 合法 SKILL.md（声明式 frontmatter + 方法正文）
    → 落盘临时目录 → SkillImporter.import_directory 正规注册
    → 本任务用上新造的 skill，后续任务也能复用

红线延续 (V1.12):
  - 声明式包禁可执行内容 — importer 三防（扩展名白名单/路径穿越/
    512KB）天然拦截
  - 来源标记 PLUGIN（区别用户手导 USER）——动态产物可整体辨识
  - skill_id 冲突（同版本已存在）→ 跳过注册直接用已有的
  - 失败绝不阻断执行（skill 是可选增强）
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_FABRICATED_DIR = "data/skills/fabricated"
_MAX_BODY_CHARS = 12000


class SkillFabricator:
    """联网搜索 → LLM 综合 → 注册为可用 skill。

    searcher 可注入（测试隔离）: None=真联网 / False=禁用 /
    callable=stub（query → results）
    """

    def __init__(self, session: AsyncSession, *, searcher=None):
        self._session = session
        self._searcher = searcher

    async def fabricate(
        self,
        *,
        task_content: str,
        task_name: str = "",
        project_id: int | None = None,
    ):
        """无可用 skill 时按任务内容联网制作一个。

        Returns: (SkillDefinition, SkillExecutionContext) 或 None
        """

        # 1) 联网搜索（复用执行体的无 key 抓取）
        query = self._build_query(task_content, task_name)
        results = await self._search(query)
        if not results:
            logger.info("skill fabrication: search empty for %r", query)
            return None

        # 2) LLM 综合 → SKILL.md
        skill_md = await self._synthesize(query, task_content, results)
        if skill_md is None:
            return None
        meta = self._parse_frontmatter(skill_md)
        if not meta or not meta.get("skill_id"):
            logger.warning("skill fabrication: synthesize produced no meta")
            return None
        skill_id = str(meta["skill_id"]).strip()

        # 3) 已注册（同 id 同版本）→ 直接复用
        from project_runtime.skills.registry import SkillRegistry

        registry = SkillRegistry(self._session)
        version = str(meta.get("version", "1.0.0")).strip() or "1.0.0"
        existing = await registry.get(skill_id, version)
        if existing is not None:
            logger.info(
                "skill fabrication: %s@%s already registered — reuse",
                skill_id, version,
            )
            return existing, None  # context 由调用方 load

        # 4) 落盘 + 正规导入（三防天然生效）
        package_dir = self._write_package(skill_md)
        try:
            from project_runtime.skills.enums import SkillSource
            from project_runtime.skills.importer import SkillPackageImporter

            importer = SkillPackageImporter(self._session)
            skill = await importer.import_directory(
                str(package_dir), source=SkillSource.PLUGIN
            )
            logger.info(
                "skill FABRICATED online: %s@%s for task %r "
                "(search: %d results)",
                skill.skill_id, skill.version, task_name[:40], len(results),
            )
            return skill, None
        except Exception:  # noqa: BLE001 — 已注册冲突等
            logger.debug("skill fabrication register failed", exc_info=True)
            return None

    # ── 内部 ─────────────────────────────────────────────────────────
    def _build_query(self, task_content: str, task_name: str) -> str:
        head = (task_name or task_content or "").strip()[:60]
        return f"{head} 实施方法 最佳实践 步骤"

    async def _search(self, query: str) -> list[dict]:
        # 注入优先（测试隔离）
        if self._searcher is False:
            return []
        if callable(self._searcher):
            return await self._searcher(query) or []

        import asyncio

        from project_runtime.research.executor import WebSearchProvider

        def _do() -> list[dict]:
            return WebSearchProvider().search(query)

        return await asyncio.wait_for(
            asyncio.to_thread(_do), timeout=30
        )

    async def _synthesize(
        self, query: str, task_content: str, results: list[dict]
    ) -> str | None:
        """LLM: 搜索结果 → 合法 SKILL.md（frontmatter + 正文）。"""
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

            context = "\n\n".join(
                f"[{i+1}] {r['title']}\n{r['content']}"
                for i, r in enumerate(results)
            )
            # 快照进线程（MissingGreenlet 防护）
            task_text = task_content[:3000]
            q_text = query

            def _blocking() -> str:
                resp = litellm.completion(
                    model=model_id,
                    api_base=cfg.base_url,
                    api_key=cfg.api_key,
                    messages=[{
                        "role": "user",
                        "content": (
                            "你是 skill 制作器。根据搜索结果为下面的任务"
                            "制作一个可复用的方法 skill 包（SKILL.md）。\n\n"
                            f"任务：{task_text[:800]}\n\n"
                            f"搜索关键词：{q_text}\n\n"
                            f"搜索结果：\n{context[:8000]}\n\n"
                            "要求输出（严格按此格式，不要输出其他内容）：\n"
                            "---\n"
                            "skill_id: kebab-case-英文id\n"
                            "name: 中文技能名\n"
                            "version: 1.0.0\n"
                            "skill_type: TASK_EXECUTION\n"
                            "description: 一句话说明\n"
                            "when_to_use: 什么场景该用这个 skill\n"
                            "---\n"
                            "# 技能名\n\n"
                            "## 适用场景\n...\n\n"
                            "## 实施步骤\n（分步操作指引，综合搜索结果）\n"
                            "...\n\n"
                            "## 验收要点\n...\n"
                        ),
                    }],
                    max_tokens=4000,
                    timeout=180,
                )
                m = resp.choices[0].message
                return m.content or (getattr(m, "reasoning_content", "") or "")[-6000:]

            raw = await asyncio.wait_for(
                asyncio.to_thread(_blocking), timeout=200
            )
            if not raw or "---" not in raw:
                return None
            # 掐头（LLM 可能加前言）
            idx = raw.find("---")
            md = raw[idx:]
            if len(md) > _MAX_BODY_CHARS + 2000:
                md = md[: _MAX_BODY_CHARS + 2000]
            return md
        except Exception:  # noqa: BLE001 — 制作失败不阻断
            logger.debug("skill synthesize failed", exc_info=True)
            return None

    def _parse_frontmatter(self, md: str) -> dict | None:
        try:
            from project_runtime.skills.importer import parse_frontmatter

            return parse_frontmatter(md)
        except Exception:  # noqa: BLE001
            return None

    def _write_package(self, skill_md: str) -> Path:
        out = Path(_FABRICATED_DIR) / f"fab_{int(time.time())}"
        if out.parent.exists():
            # 控制目录膨胀: 只留最近 20 个
            siblings = sorted(out.parent.glob("fab_*"))
            for old in siblings[:-20]:
                shutil.rmtree(old, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        (out / "SKILL.md").write_text(skill_md, encoding="utf-8")
        return out
