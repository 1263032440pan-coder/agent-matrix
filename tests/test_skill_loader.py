"""D17D Skill Loader + Worker Integration — D17.5 matrix E01-E06."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Project, Task, TaskRun
from project_runtime.skills import (
    ResolutionStatus,
    SkillLoader,
    SkillRequirement,
    SkillResolver,
    SkillType,
)
from project_runtime.skills.importer import SkillPackageImporter
from project_runtime.skills.loader import SkillLoadError
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.adapter import MockWorkerAdapter, WorkPackage

SKILL_MD = """---
skill_id: exec-skill
name: Execution Skill
version: 1.0.0
skill_type: TASK_EXECUTION
description: fixture for loader tests
---

# Execution method

Follow the steps.
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
    p = Project(name="d17d-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


@pytest.fixture
async def imported_skill(dbsession, tmp_path):
    root = tmp_path / "pkg"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "references" / "guide.md").write_text("# guide", encoding="utf-8")
    return await SkillPackageImporter(dbsession).import_directory(str(root))


# E01 — load SKILL.md
@pytest.mark.asyncio
async def test_e01_load_skill_md(dbsession, imported_skill):
    loader = SkillLoader(dbsession)
    ctx = await loader.load("exec-skill", "1.0.0")
    assert "Execution method" in ctx.skill_body
    assert ctx.skill_id == "exec-skill"
    assert ctx.skill_version == "1.0.0"
    assert len(ctx.skill_content_hash) == 64


# E02 — load only required references
@pytest.mark.asyncio
async def test_e02_load_required_references_only(dbsession, imported_skill):
    loader = SkillLoader(dbsession)
    ctx = await loader.load("exec-skill", "1.0.0", required_references=["references/guide.md"])
    assert ctx.references == {"references/guide.md": "# guide"}


# E03 — registry has many skills; loader touches only the resolved one
@pytest.mark.asyncio
async def test_e03_no_full_registry_injection(dbsession, tmp_path, imported_skill):
    # Register another skill; loader must not read its content
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(
        SKILL_MD.replace("exec-skill", "other-skill"), encoding="utf-8"
    )
    await SkillPackageImporter(dbsession).import_directory(str(other))

    loader = SkillLoader(dbsession)
    ctx = await loader.load("exec-skill", "1.0.0")
    assert "other-skill" not in ctx.skill_body


# E04 — content_hash mismatch → LOAD_FAILED
@pytest.mark.asyncio
async def test_e04_hash_mismatch(dbsession, imported_skill):
    loader = SkillLoader(dbsession)
    with pytest.raises(SkillLoadError, match="hash mismatch"):
        await loader.load("exec-skill", "1.0.0", expected_hash="0" * 64)


# E05 — Worker result tracks actual skill id/version/hash
@pytest.mark.asyncio
async def test_e05_worker_result_skill_audit(dbsession, project, imported_skill):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="t"))
    await mgr.promote_to_ready(task.id)

    resolver = SkillResolver(dbsession)
    r = await resolver.resolve(
        SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=False)
    )
    assert r.status == ResolutionStatus.RESOLVED

    loader = SkillLoader(dbsession)
    ctx = await loader.load(
        r.resolved_skill.skill_id,
        r.resolved_skill.version,
        expected_hash=r.resolved_skill.content_hash,
    )

    worker = MockWorkerAdapter(dbsession)
    result = await worker.assign(
        WorkPackage(task_id=task.id, task_name=task.name, skill_context=ctx)
    )
    assert result.actual_skill_id == "exec-skill"
    assert result.actual_skill_version == "1.0.0"
    assert result.actual_skill_content_hash == ctx.skill_content_hash

    # TaskRun audit carries skill info
    from sqlalchemy import select

    run = (
        await dbsession.execute(select(TaskRun).where(TaskRun.task_id == task.id))
    ).scalar_one()
    payload = json.loads(run.result)
    assert payload["skill"]["skill_id"] == "exec-skill"


# E06 — skill saying "ACCEPTED" does not change task state; Gate still rules
@pytest.mark.asyncio
async def test_e06_skill_output_not_accepted(dbsession, project, imported_skill):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="t"))
    await mgr.promote_to_ready(task.id)

    loader = SkillLoader(dbsession)
    ctx = await loader.load("exec-skill", "1.0.0")

    worker = MockWorkerAdapter(dbsession)
    await worker.assign(WorkPackage(task_id=task.id, task_name=task.name, skill_context=ctx))

    await dbsession.refresh(task)
    assert task.status == TaskStatus.EXECUTION_DONE  # not ACCEPTED

    # And non-GATE actor still cannot accept (D13 actor guard holds)
    fsm = StateTransitionService(dbsession)
    from project_runtime.project_state.fsm import InvalidStateTransitionError

    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_task(task, TaskStatus.ACCEPTED, actor="SKILL")


# No-skill task still executes (NO_SKILL_REQUIRED path)
@pytest.mark.asyncio
async def test_no_skill_task_executes(dbsession, project):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="plain"))
    await mgr.promote_to_ready(task.id)

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=False)
    )
    assert r.status == ResolutionStatus.NO_SKILL_REQUIRED

    worker = MockWorkerAdapter(dbsession)
    result = await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))
    assert result.success
    assert result.actual_skill_id is None
    await dbsession.refresh(task)
    assert task.status == TaskStatus.EXECUTION_DONE
