"""D07-TD01 batch-1 — idempotency: repeated calls produce zero new records."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.gap.classifier import GapClassifier
from project_runtime.method_asset.importer import MethodAssetImporter
from project_runtime.planner.planner import DynamicPlanner
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.procedure.normalizer import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode
from project_runtime.project_state.models import (
    ManualAction,
    MethodGap,
    Project,
    ProjectPlaybook,
    ProjectReadinessAssessment,
    Task,
)
from project_runtime.readiness.assessor import MethodReadinessAssessor
from project_runtime.scheduler.scheduler import Scheduler
from project_runtime.task_graph.manager import TaskGraphManager

SOP = """# 流程

## 需求

输入: X
活动: Y
输出: Z
验收: OK

## 设计

输入: Z
活动: W
依赖: 需求
验收: OK
"""


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
    p = Project(name="idem-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _count(dbsession, model) -> int:
    return (await dbsession.execute(select(func.count(model.id)))).scalar_one()


@pytest.mark.asyncio
async def test_assess_idempotent(dbsession, project, tmp_path):
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)
    model = await ProcedureNormalizer(dbsession).normalize(project.id)

    assessor = MethodReadinessAssessor(dbsession)
    a1 = await assessor.assess(project.id, model.id)
    a2 = await assessor.assess(project.id, model.id)
    a3 = await assessor.assess(project.id, model.id)

    assert a1.id == a2.id == a3.id
    assert await _count(dbsession, ProjectReadinessAssessment) == 1


@pytest.mark.asyncio
async def test_classify_idempotent(dbsession, project, tmp_path):
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)
    model = await ProcedureNormalizer(dbsession).normalize(project.id)
    assessment = await MethodReadinessAssessor(dbsession).assess(project.id, model.id)

    classifier = GapClassifier(dbsession)
    g1 = await classifier.classify(assessment.id)
    g2 = await classifier.classify(assessment.id)

    assert len(g1) == len(g2)
    assert await _count(dbsession, MethodGap) == len(g1)


@pytest.mark.asyncio
async def test_build_idempotent(dbsession, project, tmp_path):
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)
    model = await ProcedureNormalizer(dbsession).normalize(project.id)
    assessment = await MethodReadinessAssessor(dbsession).assess(project.id, model.id)
    await GapClassifier(dbsession).classify(assessment.id)

    builder = PlaybookBuilder(dbsession)
    p1 = await builder.build(project.id)
    p2 = await builder.build(project.id)

    assert p1.id == p2.id
    assert await _count(dbsession, ProjectPlaybook) == 1


@pytest.mark.asyncio
async def test_plan_idempotent(dbsession, project, tmp_path):
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)
    model = await ProcedureNormalizer(dbsession).normalize(project.id)
    assessment = await MethodReadinessAssessor(dbsession).assess(project.id, model.id)
    await GapClassifier(dbsession).classify(assessment.id)
    playbook = await PlaybookBuilder(dbsession).build(project.id)

    planner = DynamicPlanner(dbsession)
    t1 = await planner.plan(playbook)
    t2 = await planner.plan(playbook)

    assert {t.id for t in t1} == {t.id for t in t2}
    assert await _count(dbsession, Task) == len(t1)


@pytest.mark.asyncio
async def test_manual_action_no_duplicates(dbsession, tmp_path):
    project = Project(name="manual-idem", automation_mode=AutomationMode.MANUAL)
    dbsession.add(project)
    await dbsession.flush()

    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="t1"))
    await mgr.promote_to_ready(task.id)

    sched = Scheduler(dbsession, worker=None)
    await sched.dispatch_once(project.id)
    await sched.dispatch_once(project.id)
    await sched.dispatch_once(project.id)

    count = (
        await dbsession.execute(
            select(func.count(ManualAction.id)).where(ManualAction.task_id == task.id)
        )
    ).scalar_one()
    assert count == 1


# ════════════════════════════════════════════════════════════════════════════
# Evolution cases — idempotency must NOT block new inputs (返修单 P2)
# ════════════════════════════════════════════════════════════════════════════

SOP_V2 = SOP + "\n## 交付\n\n输入: app\n活动: 部署\n依赖: 开发\n验收: 签字\n"


@pytest.mark.asyncio
async def test_normalize_evolution_new_assets_new_model(dbsession, project, tmp_path):
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)

    normalizer = ProcedureNormalizer(dbsession)
    m1 = await normalizer.normalize(project.id)
    m1_again = await normalizer.normalize(project.id)
    assert m1.id == m1_again.id  # idempotent

    # Asset set changes → new model
    f2 = tmp_path / "sop2.md"
    f2.write_text(SOP_V2, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f2)
    m2 = await normalizer.normalize(project.id)
    assert m2.id != m1.id


@pytest.mark.asyncio
async def test_evolution_chain_new_playbook_new_tasks(dbsession, project, tmp_path):
    """Full evolution: new asset → new model → new assessment → new playbook →
    new task set (返修单 P1 regression)."""
    f = tmp_path / "sop.md"
    f.write_text(SOP, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f)

    normalizer = ProcedureNormalizer(dbsession)
    assessor = MethodReadinessAssessor(dbsession)
    classifier = GapClassifier(dbsession)
    builder = PlaybookBuilder(dbsession)
    planner = DynamicPlanner(dbsession)

    m1 = await normalizer.normalize(project.id)
    a1 = await assessor.assess(project.id, m1.id)
    await classifier.classify(a1.id)
    pb1 = await builder.build(project.id)
    await planner.plan(pb1)
    pb_count_1 = await _count(dbsession, ProjectPlaybook)
    task_count_1 = await _count(dbsession, Task)

    # Evolve: second asset arrives
    f2 = tmp_path / "sop2.md"
    f2.write_text(SOP_V2, encoding="utf-8")
    await MethodAssetImporter(dbsession).import_file(project.id, f2)

    m2 = await normalizer.normalize(project.id)
    assert m2.id != m1.id  # new model

    a2 = await assessor.assess(project.id, m2.id)
    assert a2.id != a1.id  # new assessment (different model_id key)

    await classifier.classify(a2.id)
    pb2 = await builder.build(project.id)
    assert pb2.id != pb1.id  # NEW playbook — evolution not blocked
    assert await _count(dbsession, ProjectPlaybook) == pb_count_1 + 1

    t2 = await planner.plan(pb2)
    # plan reuses tasks by name (same project), but new playbook drives planning
    assert t2
    assert await _count(dbsession, Task) >= task_count_1
