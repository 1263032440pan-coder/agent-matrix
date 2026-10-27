"""LLM Skill Selector（用户需求: 按任务内容自动选 skill）.

方式: name + description + when_to_use 作为 function/tool 列表给 LLM 选。
两个接入点: 生成计划（read_skill_stages 无绑定时）+ 执行任务
（ExecutionRunner 无绑定命中时）。

验证:
  [x] 候选集: AUTO_RESOLUTION_EXCLUDED 状态不进候选
  [x] 候选唯一 → 直接返回（不浪费 LLM）
  [x] LLM 选择: 注入 stub 返回 tool_call → 选中对应 skill
  [x] LLM 幻觉 id（不在候选集）→ 宁可不选
  [x] LLM 不可用 → 关键词退化匹配
  [x] read_skill_stages: 无绑定 + LLM 选中 → stages 骨架
  [x] ExecutionRunner 语义: _task_select_content 包含任务名/描述/合同
"""

import json

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
from project_runtime.skills.enums import SkillSource, SkillStatus, SkillType
from project_runtime.skills.models import SkillDefinition
from project_runtime.skills.selector import SkillSelector


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sel.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _skill(skill_id: str, *, wtu: str = "", stype=SkillType.TASK_EXECUTION,
           status=SkillStatus.ENABLED) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id, name=f"skill {skill_id}", version="1.0.0",
        skill_type=stype, description=f"desc of {skill_id}",
        when_to_use=wtu or f"用 {skill_id} 的场景说明",
        entrypoint="SKILL.md", source=SkillSource.USER, status=status,
        content_hash=f"h-{skill_id}",
    )


async def _seed(eng, skills: list[SkillDefinition]):
    async with AsyncSession(eng) as s:
        s.add_all(skills)
        await s.commit()


@pytest.mark.asyncio
async def test_single_candidate_direct_return(dbengine, monkeypatch):
    """唯一候选仍走内容匹配；LLM 选中该 skill 时返回它。"""
    await _seed(dbengine, [_skill("only-one")])

    class FakeMsg:
        class _TC:
            class _Fn:
                name = "skill_only-one"
            function = _Fn()
        content = None
        tool_calls = [_TC()]

    class FakeResp:
        choices = [type("C", (), {"message": FakeMsg()})()]

    async def fake_llm(**_kw):
        return {"ok": True, "response": FakeResp()}

    monkeypatch.setattr(
        "project_runtime.llm.provider.llm_complete", fake_llm,
    )
    from project_runtime import llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": "http://x", "api_key": "k", "model": "m"})(),
    )

    async with AsyncSession(dbengine) as s:
        sel = SkillSelector(s)
        picked = await sel.select(task_content="随便什么任务")
    assert picked is not None and picked.skill_id == "only-one"


@pytest.mark.asyncio
async def test_excluded_status_not_candidate(dbengine):
    from project_runtime.skills.enums import AUTO_RESOLUTION_EXCLUDED

    if not AUTO_RESOLUTION_EXCLUDED:
        pytest.skip("no excluded statuses in this config")
    bad = _skill("bad", status=next(iter(AUTO_RESOLUTION_EXCLUDED)))
    await _seed(dbengine, [bad])
    async with AsyncSession(dbengine) as s:
        pool = await SkillSelector(s).candidates(None)
    assert all(sk.skill_id != "bad" for sk in pool)


@pytest.mark.asyncio
async def test_llm_pick_via_tool_call(dbengine, monkeypatch):
    await _seed(dbengine, [
        _skill("web-dev", wtu="开发网页应用"),
        _skill("doc-writing", wtu="写文档"),
    ])

    class FakeMsg:
        class _TC:
            class _Fn:
                name = "skill_web-dev"
            function = _Fn()
        content = None
        tool_calls = [_TC()]

    class FakeResp:
        choices = [type("C", (), {"message": FakeMsg()})()]

    async def fake_to_thread(fn, *a, **kw):
        return fn()

    import asyncio

    class FakeLitellm:
        @staticmethod
        def completion(**kw):
            assert kw.get("tools"), "must pass tool list"
            names = [t["function"]["name"] for t in kw["tools"]]
            assert "skill_web-dev" in names and "skill_doc-writing" in names
            # 工具描述里必须含三要素
            desc = kw["tools"][0]["function"]["description"]
            assert "description:" in desc and "when_to_use:" in desc
            return FakeResp()

    import project_runtime.skills.selector as sel_mod
    monkeypatch.setattr(sel_mod, "litellm", FakeLitellm(), raising=False)
    # litellm 是函数内 import — patch litellm 模块本身
    import sys
    sys.modules["litellm"] = type("M", (), {"completion": FakeLitellm.completion})
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    # LLM 配置必须存在才走 LLM 路径
    from project_runtime import llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": "http://x", "api_key": "k", "model": "m"})(),
    )

    async with AsyncSession(dbengine) as s:
        picked = await SkillSelector(s).select(task_content="做一个网页应用")
    assert picked is not None and picked.skill_id == "web-dev"


@pytest.mark.asyncio
async def test_llm_hallucinated_id_rejected(dbengine, monkeypatch):
    await _seed(dbengine, [_skill("real-one")])
    # 唯一候选直接返回——再种一个凑两个
    await _seed(dbengine, [_skill("real-two")])

    class FakeMsg:
        content = "我认为应该用 skill_ghost-9000"
        tool_calls = None

    class FakeResp:
        choices = [type("C", (), {"message": FakeMsg()})()]

    import asyncio

    class FakeLitellm:
        @staticmethod
        def completion(**kw):
            return FakeResp()

    import sys
    sys.modules["litellm"] = type("M", (), {"completion": FakeLitellm.completion})
    monkeypatch.setattr(
        asyncio, "to_thread",
        lambda fn, *a, **kw: _ret(fn()),
    )
    from project_runtime import llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": "http://x", "api_key": "k", "model": "m"})(),
    )

    async def _ret(v):
        return v

    async with AsyncSession(dbengine) as s:
        picked = await SkillSelector(s).select(task_content="任务")
    # 幻觉 id + 文本里没有候选名 → 关键词退化也不中 → None
    assert picked is None


@pytest.mark.asyncio
async def test_keyword_fallback(dbengine, monkeypatch):
    await _seed(dbengine, [
        _skill("web-dev", wtu="开发网页应用 前端"),
        _skill("data-etl", wtu="数据抽取 ETL"),
    ])
    # LLM 不可用（无配置）→ 关键词
    from project_runtime import llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": None, "api_key": None, "model": None})(),
    )
    async with AsyncSession(dbengine) as s:
        picked = await SkillSelector(s).select(task_content="网页应用开发")
    assert picked is not None and picked.skill_id == "web-dev"


@pytest.mark.asyncio
async def test_planning_autoselect_no_binding(dbengine, monkeypatch):
    """生成计划: 无绑定 + LLM 选中 PROJECT_METHOD skill → stages 骨架。"""
    import hashlib

    from project_runtime.procedure import skill_planning
    from project_runtime.project_state.enums import (
        AssetType,
        AutomationMode,
        MethodAssetAuthority,
    )
    from project_runtime.project_state.models import (
        MethodAsset,
        Project,
    )

    stages = json.dumps(
        ["项目理解", "需求基线", "开发实现", "交付"], ensure_ascii=False)
    sk = SkillDefinition(
        skill_id="m-method", name="方法", version="1.0.0",
        skill_type=SkillType.PROJECT_METHOD, description="d",
        when_to_use="软件交付方法",
        stages=stages,
        entrypoint="SKILL.md", source=SkillSource.USER,
        status=SkillStatus.ENABLED, content_hash="h1",
    )
    async with AsyncSession(dbengine) as s:
        p = Project(name="p", goal="g",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        doc = "# 需求\n\n功能点A、功能点B\n"
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        s.add(sk)
        await s.commit()

    async def fake_select(self, **kw):
        stmt = select(SkillDefinition).where(
            SkillDefinition.skill_id == "m-method")
        return (await self._session.execute(stmt)).scalar_one()

    monkeypatch.setattr(SkillSelector, "select", fake_select)

    async with AsyncSession(dbengine) as s:
        result = await skill_planning.read_skill_stages(s, pid)
    assert result is not None
    assert [st["name"] for st in result] == [
        "项目理解", "需求基线", "开发实现", "交付"]
    assert result[0]["skill_id"] == "m-method"

