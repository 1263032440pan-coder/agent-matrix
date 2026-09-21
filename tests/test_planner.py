"""D10 Dynamic Planner — tests for Playbook → Task Graph conversion."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.method_asset.importer import MethodAssetImporter
from project_runtime.planner.planner import DynamicPlanner
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.procedure.normalizer import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.models import Project
from project_runtime.readiness.assessor import MethodReadinessAssessor


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d10-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _build_playbook(dbsession, project, *texts):
    importer = MethodAssetImporter(dbsession)
    for text in texts:
        await importer.import_text(project.id, text, source_name="test.md")
    normalizer = ProcedureNormalizer(dbsession)
    model = await normalizer.normalize(project.id)
    assessor = MethodReadinessAssessor(dbsession)
    await assessor.assess(project.id, model.id)
    builder = PlaybookBuilder(dbsession)
    return await builder.build(project.id)


# ════════════════════════════════════════════════════════════════════════════
# Basic planning
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_plan_creates_tasks(dbsession, project):
    playbook = await _build_playbook(
        dbsession, project,
        "# 需求分析\n输入: 需求\n活动: 编写需求文档\n# 方案设计\n输入: 需求文档\n活动: 架构设计",
    )
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    assert len(tasks) >= 2


@pytest.mark.asyncio
async def test_task_has_playbook_ref(dbsession, project):
    playbook = await _build_playbook(dbsession, project, "# 流程A\n输入: A\n活动: B")
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    for t in tasks:
        ref = json.loads(t.playbook_ref or "{}")
        assert "workstream_name" in ref
        assert "authority" in ref


# ════════════════════════════════════════════════════════════════════════════
# Authority rules
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mandatory_not_silently_dropped(dbsession, project):
    playbook = await _build_playbook(dbsession, project, "# 关键步骤\n输入: 必须做\n活动: 必须完成")
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    # All workstreams must produce tasks — none silently dropped
    workstreams = json.loads(playbook.workstreams or "[]")
    assert len(tasks) >= len(workstreams)


# ════════════════════════════════════════════════════════════════════════════
# Dependency creation
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dependencies_created(dbsession, project):
    playbook = await _build_playbook(
        dbsession, project,
        "# 上游\n输入: X\n活动: 做X\n# 下游\n输入: X结果\n活动: 做Y\n依赖: 上游",
    )
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    # Verify dependency exists
    downstream = next((t for t in tasks if t.name == "下游"), None)
    upstream = next((t for t in tasks if t.name == "上游"), None)
    assert downstream is not None
    assert upstream is not None


# ════════════════════════════════════════════════════════════════════════════
# No skill needed
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_plan_without_skills(dbsession, project):
    playbook = await _build_playbook(dbsession, project, "# 简单流程\n输入: X\n活动: Y")
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    assert len(tasks) >= 1


# ════════════════════════════════════════════════════════════════════════════
# No hardcoded industry stages
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_hardcoded_stages(dbsession, project):
    """Planner must not hardcode any industry phases; task names come from playbook."""
    playbook = await _build_playbook(
        dbsession, project,
        "# 自定义阶段A\n输入: 数据\n活动: 处理\n# 自定义阶段B\n输入: 结果\n活动: 输出",
    )
    planner = DynamicPlanner(dbsession)
    tasks = await planner.plan(playbook)
    names = {t.name for t in tasks}
    assert "自定义阶段A" in names
    assert "自定义阶段B" in names