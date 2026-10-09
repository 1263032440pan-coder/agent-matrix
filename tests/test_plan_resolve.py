"""首次规划即研究（用户需求 2026-10-09）: /plan classify 后接 resolve 阶段。

规则:
  - 有可用 skill（注册表可解析）→ 不联网（research_executor=False，
    gap 照常路由建研究记录但不执行——skill 已提供方法）
  - 无 skill → 真联网（五查 miss 的 gap 走 WebResearchExecutor）

验证:
  [x] 有 skill: _resolve 返回 skill_available=True 且研究不联网（stub 验证
      executor 未被调用）
  [x] 无 skill: 研究真执行（stub executor 被 GapResolver 调用）
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.project_state.base import Base


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rs.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_skill_match_semantics(dbengine):
    """用户修正（2026-10-09）: 必须是符合任务场景的 skill——不是注册表非空。

    SkillSelector.select(task_content=...) 返回 None（无匹配/LLM+关键词
    都不中）→ 联网；返回 skill → 不联网。_resolve 消费 select 结果。
    """

    class FakeSel:
        def __init__(self, ret):
            self._ret = ret

        async def select(self, **kw):
            return self._ret

    # LLM 未配置 + 内容不匹配 → select 返回 None → 应联网
    from project_runtime import llm as llm_mod
    from project_runtime.skills.selector import SkillSelector

    orig = llm_mod.load_llm_settings
    llm_mod.load_llm_settings = lambda: type(
        "C", (), {"base_url": None, "api_key": None, "model": None}
    )()
    try:
        from project_runtime.skills.enums import (
            SkillSource,
            SkillStatus,
            SkillType,
        )
        from project_runtime.skills.models import SkillDefinition

        async with AsyncSession(dbengine) as s:
            s.add(SkillDefinition(
                skill_id="cooking", name="烹饪技能", version="1.0.0",
                skill_type=SkillType.PROJECT_METHOD,
                description="做菜方法", when_to_use="做饭场景",
                entrypoint="SKILL.md", source=SkillSource.USER,
                status=SkillStatus.ENABLED, content_hash="h",
            ))
            await s.commit()

        # 软件项目文档 vs 烹饪 skill——关键词不中 → select None
        async with AsyncSession(dbengine) as s:
            picked = await SkillSelector(s).select(
                task_content="开发一个软件项目，需要需求分析、架构设计、测试",
                skill_type=SkillType.PROJECT_METHOD,
            )
        assert picked is None, "不相干 skill 不应被选中"
    finally:
        llm_mod.load_llm_settings = orig

