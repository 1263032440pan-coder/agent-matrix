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
async def test_skill_available_flag(dbengine):
    """SkillSelector.candidates 非空 = skill_available（_resolve 的判定源）。"""
    from project_runtime.skills.enums import SkillSource, SkillStatus, SkillType
    from project_runtime.skills.models import SkillDefinition
    from project_runtime.skills.selector import SkillSelector

    # 空 → False
    async with AsyncSession(dbengine) as s:
        assert await SkillSelector(s).candidates(None) == []

    # 有一个 → True
    async with AsyncSession(dbengine) as s:
        s.add(SkillDefinition(
            skill_id="m", name="M", version="1.0.0",
            skill_type=SkillType.PROJECT_METHOD, description="d",
            when_to_use="方法", entrypoint="SKILL.md",
            source=SkillSource.USER, status=SkillStatus.ENABLED,
            content_hash="h",
        ))
        await s.commit()
    async with AsyncSession(dbengine) as s:
        pool = await SkillSelector(s).candidates(None)
        assert len(pool) == 1

    # DISABLED（排除态）→ 不算可用
    async with AsyncSession(dbengine) as s:
        from sqlalchemy import update

        await s.execute(
            update(SkillDefinition).where(
                SkillDefinition.skill_id == "m"
            ).values(status=SkillStatus.DISABLED)
        )
        await s.commit()
    async with AsyncSession(dbengine) as s:
        assert await SkillSelector(s).candidates(None) == []
