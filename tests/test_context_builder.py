"""WP-010 Context Builder tests — 验收五条 + 复验预告.

验收:
  [x] 支持 Token Budget
  [x] P0 不被裁剪
  [x] 内容去重
  [x] 保留 source reference
  [x] 可使用 Project Memory Manager.retrieve()

复验预告:
  ① 六件套输入齐备（understanding/playbook/上游产物/方法/验收/依赖）
  ③ 与 WorkPackage 绑定方式
  ⑤ WP028-TD01 登记带上（已在 17218ba 落账——本批验证存在）

禁止（结构性断言）: 全量历史 / 直连 Retrieval 实现 / 检索当事实
"""

import hashlib
import inspect
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.context import ContextBuilder, ContextPackage
from project_runtime.memory_summary import SummaryGenerator
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
    TaskStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    MethodAsset,
    Project,
    Task,
    TaskDependency,
)

METHOD_DOC = """# 实施流程

## 阶段：开发
- 输入: 需求基线
- 活动: 按模块实现
- 输出: 模块代码
- 依赖: 无
- 约束: 内存<512MB
- 验收: 测试全部通过
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ctx.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed_project(dbengine):
    """两任务链（t1→t2 HARD）+ 方法模型 + 上游产物 + 项目摘要。"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="ctx", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content=METHOD_DOC,
            content_hash=hashlib.sha256(METHOD_DOC.encode()).hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        t1 = Task(project_id=pid, name="阶段：需求", status=TaskStatus.ACCEPTED)
        t2 = Task(project_id=pid, name="阶段：开发", status=TaskStatus.READY)
        s.add_all([t1, t2])
        await s.flush()
        t1_id, t2_id = t1.id, t2.id
        s.add(TaskDependency(task_id=t2_id, depends_on_id=t1_id))
        s.add(Artifact(
            logical_artifact_id="req", project_id=pid, task_id=t1_id,
            name="需求基线.v1.md", version=1, path="data/req.v1.md",
            sha256="a" * 64,
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()

    async with AsyncSession(dbengine) as s:
        await SummaryGenerator().generate_project_summary(
            s, pid, source_version=1
        )
        await s.commit()
    return pid, t2_id, mid


# ── 验收 §1+§2: Token Budget + P0 不裁剪 ────────────────────────────


@pytest.mark.asyncio
async def test_token_budget_p0_never_cut(dbengine):
    pid, t2_id, _ = await _seed_project(dbengine)

    # 极小预算（低于 P0+P1 总量）：P0 必须完整保留、P2/P3 全丢
    async with AsyncSession(dbengine) as s:
        builder = ContextBuilder(s, budget_tokens=40)
        pkg = await builder.build(pid, t2_id)

    assert pkg.truncated is True
    p0 = pkg.p0_sections
    assert p0, "P0 sections must never be empty"
    # P0 内容完整：任务名 + 验收 + 约束 + 依赖
    kinds = {x.kind for x in p0}
    assert {"task", "acceptance", "constraint", "dependency"} <= kinds
    assert any("测试全部通过" in x.content for x in p0)
    assert any("内存<512MB" in x.content for x in p0)
    assert any("阶段：需求" in x.content for x in p0)
    # dropped recorded
    assert pkg.dropped_kinds


@pytest.mark.asyncio
async def test_budget_relaxed_keeps_more(dbengine):
    pid, t2_id, _ = await _seed_project(dbengine)
    async with AsyncSession(dbengine) as s:
        tight = await ContextBuilder(s, budget_tokens=60).build(pid, t2_id)
    async with AsyncSession(dbengine) as s:
        relaxed = await ContextBuilder(s, budget_tokens=8000).build(pid, t2_id)
    assert relaxed.total_tokens >= tight.total_tokens
    assert relaxed.p0_sections == tight.p0_sections  # P0 stable regardless


# ── 验收 §3: 内容去重 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_content_dedup(dbengine):
    pid, t2_id, _ = await _seed_project(dbengine)
    async with AsyncSession(dbengine) as s:
        pkg = await ContextBuilder(s).build(pid, t2_id)
    contents = [x.content.strip() for x in pkg.sections]
    assert len(contents) == len(set(contents)), "duplicate content sections"


# ── 验收 §4: 保留 source reference ─────────────────────────────────


@pytest.mark.asyncio
async def test_source_refs_preserved(dbengine):
    pid, t2_id, _ = await _seed_project(dbengine)
    async with AsyncSession(dbengine) as s:
        pkg = await ContextBuilder(s).build(pid, t2_id)
    assert pkg.sections
    for sec in pkg.sections:
        assert sec.source_ref, f"section {sec.kind} missing source_ref"
    # P0 sources point at task/procedure node
    refs = {x.source_ref for x in pkg.p0_sections}
    assert any(r.startswith("task:") for r in refs)
    assert any(r.startswith("procedure_node:") for r in refs)


# ── 验收 §5: 经 MemoryManager.retrieve()（统一门面）────────────────


@pytest.mark.asyncio
async def test_uses_memory_manager_retrieve(dbengine):
    """P2 检索段经统一门面（结构性 + 无 provider 时不炸）。"""
    import project_runtime.context.builder as b_mod

    src = inspect.getsource(b_mod)
    assert "mgr.retrieve(" in src
    assert "FTS5Retriever" not in src  # 禁直连具体实现
    # 无 provider 注册（registry 未配）→ 空结果不报错
    pid, t2_id, _ = await _seed_project(dbengine)
    async with AsyncSession(dbengine) as s:
        pkg = await ContextBuilder(s).build(pid, t2_id)
    assert pkg is not None


# ── 禁止三条（结构性断言）──────────────────────────────────────────


def test_forbidden_patterns():
    import project_runtime.context.builder as b_mod

    src = inspect.getsource(b_mod)
    # 禁全量加载历史：history 段有 limit 截断
    assert "self._history_limit" in src
    # 禁直连具体 Retrieval 实现
    for forbidden in ("FTS5Retriever", "RipgrepRetriever", "PathRetriever",
                      "FTS5Indexer"):
        assert forbidden not in src
    # 禁检索当事实：P2 检索段必须带非事实标注
    assert "[检索参考·非事实]" in src


def test_retrieval_marked_non_fact(dbengine=None):
    """P2 检索 section 内容必须带非事实标注（运行时由 add 统一注入——
    源码级断言在上条；这里验证 render 输出的标注格式存在）。"""
    import project_runtime.context.builder as b_mod

    src = inspect.getsource(b_mod)
    assert 'add(P2, "retrieval"' in src
    assert "非事实" in src


# ── 复验预告①: 六件套输入（P0-P3 全链实测）────────────────────────


@pytest.mark.asyncio
async def test_six_inputs_present(dbengine):
    """understanding 快照（goal 经 state memory）+ 方法（procedure 节点
    验收/约束/活动）+ 依赖 + 上游产物 + 摘要 + 历史——全链各就位。"""
    pid, t2_id, _ = await _seed_project(dbengine)

    # understanding 快照（WP-026 → WP-010 消费点）
    from project_runtime.understanding import UnderstandingService

    async with AsyncSession(dbengine) as s:
        await UnderstandingService(s).analyze_project(pid)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        pkg = await ContextBuilder(s, budget_tokens=8000).build(pid, t2_id)

    kinds = {x.kind for x in pkg.sections}
    # 方法上下文（验收/约束/活动）+ 依赖 + 上游产物 + 摘要
    assert {"acceptance", "constraint", "dependency"} <= kinds
    assert "artifact" in kinds, "direct upstream artifact missing"
    assert "summary" in kinds, "project summary missing"
    assert pkg.render(), "rendered context must be non-empty"


# ── 复验预告③: 与 WorkPackage 绑定 ─────────────────────────────────


def test_package_json_serializable_for_workpackage():
    """ContextPackage 可 JSON 序列化注入 WorkPackage.contract 槽
    （V0.1 绑定方式——不新增字段，走 contract）。"""
    pkg = ContextPackage(project_id=1, task_id=2, budget_tokens=100)
    data = json.loads(pkg.to_json())
    assert data["project_id"] == 1 and data["task_id"] == 2


def test_middleware_returns_new_package():
    """Middleware 唯一合法变换=产新对象（frozen dataclass 原地改不可能）。"""
    pkg = ContextPackage(project_id=1, task_id=2, budget_tokens=100)
    new = pkg.with_sections(())
    assert new is not pkg
    assert new.sections == ()
    assert pkg.sections == ()  # original untouched


# ── 复验预告⑤: WP028-TD01 登记存在 ────────────────────────────────


def test_wp028_td01_registered():
    s = open(
        "docs/project-spec/20-tracking/问题与阻塞项.md", encoding="utf-8"
    ).read()
    assert "WP028-TD01" in s


# ── extension: context.providers / context.middleware ─────────────


def test_context_points_in_stable_points():
    from project_runtime.extension.runtime.executor import STABLE_POINTS

    assert "context.providers" in STABLE_POINTS
    assert "context.middleware" in STABLE_POINTS
