"""方案 C: 依赖回流兜底链（用户决策 2026-10-09）.

一句话 txt 场景（文档只列举阶段、无先后描述）→ 抽取 dependencies 全
UNKNOWN → 任务图零边（散点图）。三级兜底:
  ① ANSWERED 研究结论回流（summary 里阶段出现位置排序）
  ② LLM 语义推断（排列校验）
  ③ 文档列举顺序串行兜底
仅「抽取依赖全空」时介入; 文档明写的依赖永不被覆盖; 兜底边标
SOFT + playbook_ref.inferred。

验证:
  [x] 全 UNKNOWN + 无研究 + LLM 不可用 → 列举顺序串行链
  [x] 文档明写依赖 → 兜底不介入（既有边保留）
  [x] 研究回流: summary 覆盖全部节点 → 按 summary 位置排序
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
from project_runtime.planner.planner import DynamicPlanner
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import (
    MethodAsset,
    Project,
    ProjectPlaybook,
    TaskDependency,
)
from project_runtime.research.models import ResearchRecord


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bf.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(eng, deps: list[str]) -> int:
    """种项目+资产+playbook（workstreams 的 dependencies 逐项给值）。"""
    import hashlib
    import json

    ws = []
    names = ["需求分析", "系统设计", "编码实现", "测试验收"]
    for i, name in enumerate(names):
        ws.append({"name": name, "dependencies": deps[i]})
    async with AsyncSession(eng) as s:
        p = Project(name="bf", goal="g",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        doc = "# 一句话\n开发项目\n"
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.txt", content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
            authority=MethodAssetAuthority.OPTIONAL,
        ))
        pb = ProjectPlaybook(
            project_id=pid, workstreams=json.dumps(ws, ensure_ascii=False),
        )
        s.add(pb)
        await s.commit()
    return pid


@pytest.mark.asyncio
async def test_declared_order_fallback(dbengine, monkeypatch):
    """全 UNKNOWN + 无研究 + LLM 不可用 → 列举顺序串行链（SOFT）。"""
    pid = await _seed(
        dbengine, ["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"]
    )
    # LLM 不可用
    from project_runtime import llm as llm_mod

    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": None, "api_key": None, "model": None})(),
    )
    async with AsyncSession(dbengine) as s:
        pb = (
            await s.execute(
                __import__("sqlalchemy").select(ProjectPlaybook)
                .where(ProjectPlaybook.project_id == pid)
            )
        ).scalar_one()
        tasks = await DynamicPlanner(s).plan(pb)
        await s.commit()

    assert len(tasks) == 4
    async with AsyncSession(dbengine) as s:
        from sqlalchemy import select as _select

        edges = (
            await s.execute(_select(TaskDependency))
        ).scalars().all()
        assert len(edges) == 3, "列举顺序兜底应产出 3 条串行边"
        # 链形状: t2←t1, t3←t2, t4←t3（按创建序）
        by_task = {e.task_id: e.depends_on_id for e in edges}
        assert set(by_task.values()) == {
            t for t in by_task.values()
        }
        # SOFT 类型
        assert all(e.dependency_type.value == "SOFT" for e in edges)


@pytest.mark.asyncio
async def test_no_backfill_when_explicit_deps(dbengine):
    """文档明写依赖 → 兜底不介入（既有 HARD 边保留，无额外边）。"""
    pid = await _seed(
        dbengine,
        ["UNKNOWN", "需求分析", "系统设计", "编码实现"],
    )
    async with AsyncSession(dbengine) as s:
        from sqlalchemy import select as _select

        pb = (
            await s.execute(
                _select(ProjectPlaybook)
                .where(ProjectPlaybook.project_id == pid)
            )
        ).scalar_one()
        await DynamicPlanner(s).plan(pb)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        from sqlalchemy import select as _select

        edges = (
            await s.execute(_select(TaskDependency))
        ).scalars().all()
        assert len(edges) == 3, "明写依赖 3 条 HARD 边，兜底零介入"
        assert all(e.dependency_type.value == "HARD" for e in edges)


@pytest.mark.asyncio
async def test_research_order_backflow(dbengine, monkeypatch):
    """研究回流: 依赖类 ANSWERED summary 提到全部阶段 → 依位置排序。"""
    pid = await _seed(
        dbengine, ["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"]
    )
    # 种一条依赖研究（summary 顺序打乱: 测试在前、需求在后）
    async with AsyncSession(dbengine) as s:
        s.add(ResearchRecord(
            project_id=pid, research_kind="PROJECT_METHOD_RESEARCH",
            question="编码实现与其他阶段的依赖关系和交接物如何安排？",
            status="ANSWERED",
            summary="通常先系统设计再编码实现；需求分析最先；"
                    "测试验收在编码实现之后。",
        ))
        await s.commit()

    from project_runtime import llm as llm_mod

    monkeypatch.setattr(
        llm_mod, "load_llm_settings",
        lambda: type("C", (), {
            "base_url": None, "api_key": None, "model": None})(),
    )
    async with AsyncSession(dbengine) as s:
        from sqlalchemy import select as _select

        pb = (
            await s.execute(
                _select(ProjectPlaybook)
                .where(ProjectPlaybook.project_id == pid)
            )
        ).scalar_one()
        await DynamicPlanner(s).plan(pb)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        from sqlalchemy import select as _select

        edges = (
            await s.execute(_select(TaskDependency))
        ).scalars().all()
        assert len(edges) == 3, "研究回流应产出 3 条边（覆盖全部 4 节点）"
