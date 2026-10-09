"""Online Skill Fabricator（用户需求: 无可用 skill 时联网制作 skill）.

验证（stub 搜索 + stub LLM —— 测试不联网）:
  [x] 联网搜索空 → None（不制作）
  [x] LLM 产出合法 SKILL.md → 正规注册（importer 三防路径）
  [x] skill_id 已注册 → 复用已有的（幂等）
  [x] 产出包落盘 data/skills/fabricated/（SKILL.md 可读）
  [x] 来源标记 PLUGIN（区别用户手导 USER）
  [x] frontmatter 必含 when_to_use（供 LLM 选择器消费）
"""

import pytest
from sqlalchemy import select
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
from project_runtime.skills.enums import SkillSource, SkillType
from project_runtime.skills.fabricator import SkillFabricator
from project_runtime.skills.models import SkillDefinition

_GOOD_MD = """---
skill_id: fab-test-canvas-skill
name: Canvas 编辑器集成方法
version: 1.0.0
skill_type: TASK_EXECUTION
description: 基于 Canvas 的富文本编辑器集成步骤
when_to_use: 需要在 Web 应用中集成 Canvas 富文本编辑器时
---

# Canvas 编辑器集成方法

## 适用场景
需要 Canvas 渲染的文档编辑器接入。

## 实施步骤
1. 安装 canvas-editor 依赖
2. 初始化编辑器实例并挂载
3. 配置 JSON 数据模型双向通道

## 验收要点
- 编辑内容可保存与恢复
"""


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    # fabricated 目录指向 tmp（不污染 data/）
    monkeypatch.setattr(
        "project_runtime.skills.fabricator._FABRICATED_DIR",
        str(tmp_path / "fab"),
    )
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fb.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_empty_search_no_fabrication(dbengine, monkeypatch):
    async def fake_search(self, query):
        return []

    monkeypatch.setattr(SkillFabricator, "_search", fake_search)
    async with AsyncSession(dbengine) as s:
        result = await SkillFabricator(s).fabricate(
            task_content="做某事", task_name="某任务"
        )
    assert result is None


@pytest.mark.asyncio
async def test_fabricate_registers_skill(dbengine, monkeypatch):
    results = [{"title": "canvas-editor 指南", "content": "步骤……",
                "source": "https://example.com/a"}]

    async def fake_search(self, query):
        return results

    async def fake_synthesize(self, query, tc, rs):
        return _GOOD_MD

    monkeypatch.setattr(SkillFabricator, "_search", fake_search)
    monkeypatch.setattr(SkillFabricator, "_synthesize", fake_synthesize)

    async with AsyncSession(dbengine) as s:
        fab = await SkillFabricator(s).fabricate(
            task_content="集成 Canvas 富文本编辑器", task_name="编辑器集成"
        )
        assert fab is not None
        # 快照（commit 后 ORM 过期——老坑）
        snap = {
            "skill_id": fab[0].skill_id,
            "skill_type": fab[0].skill_type,
            "source": fab[0].source,
            "when_to_use": fab[0].when_to_use or "",
        }
        await s.commit()

    assert snap["skill_id"] == "fab-test-canvas-skill"
    assert snap["skill_type"] == SkillType.TASK_EXECUTION
    assert snap["source"] == SkillSource.PLUGIN
    assert "Canvas" in snap["when_to_use"]

    # 包落盘且可加载（正规 import 链路）
    import pathlib

    fab_dir = pathlib.Path(str(dbengine.url).split("///")[1]).parent / "fab"
    skill_md_files = list(fab_dir.glob("fab_*/SKILL.md"))
    assert skill_md_files, "package must be written to fabricated dir"
    body = skill_md_files[0].read_text(encoding="utf-8")
    assert "canvas-editor" in body

    # 已注册（注册表可查）
    async with AsyncSession(dbengine) as s:
        stmt = select(SkillDefinition).where(
            SkillDefinition.skill_id == "fab-test-canvas-skill")
        got = (await s.execute(stmt)).scalar_one()
        assert got.package_ref, "package_ref must point at the package dir"
        assert got.content_hash


@pytest.mark.asyncio
async def test_duplicate_id_reuses_existing(dbengine, monkeypatch):
    async def fake_search(self, query):
        return [{"title": "t", "content": "c", "source": "s"}]

    async def fake_synthesize(self, query, tc, rs):
        return _GOOD_MD

    monkeypatch.setattr(SkillFabricator, "_search", fake_search)
    monkeypatch.setattr(SkillFabricator, "_synthesize", fake_synthesize)

    # 第一次制作
    async with AsyncSession(dbengine) as s:
        await SkillFabricator(s).fabricate(
            task_content="x", task_name="y")
        await s.commit()
    # 第二次（同 id 同版本）→ 复用
    async with AsyncSession(dbengine) as s:
        fab2 = await SkillFabricator(s).fabricate(
            task_content="x", task_name="y")
        await s.commit()
    assert fab2 is not None
    stmt_cnt = (
        select(SkillDefinition)
        .where(SkillDefinition.skill_id == "fab-test-canvas-skill")
    )
    async with AsyncSession(dbengine) as s:
        rows = (await s.execute(stmt_cnt)).scalars().all()
    assert len(rows) == 1, "duplicate must reuse, not register twice"


@pytest.mark.asyncio
async def test_bad_synthesis_rejected(dbengine, monkeypatch):
    async def fake_search(self, query):
        return [{"title": "t", "content": "c", "source": "s"}]

    async def fake_synthesize(self, query, tc, rs):
        return "没有 frontmatter 的垃圾输出"

    monkeypatch.setattr(SkillFabricator, "_search", fake_search)
    monkeypatch.setattr(SkillFabricator, "_synthesize", fake_synthesize)

    async with AsyncSession(dbengine) as s:
        result = await SkillFabricator(s).fabricate(
            task_content="x", task_name="y")
    assert result is None
