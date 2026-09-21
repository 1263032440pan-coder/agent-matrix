"""Tests for Project State domain entities and repositories."""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from infrastructure.repository.base import BaseRepository
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AutomationMode,
    DependencyType,
    ProjectStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    Checkpoint,
    Constraint,
    Decision,
    DeliveryContract,
    DeliveryItem,
    Event,
    GateResult,
    HumanDecision,
    Issue,
    ManualAction,
    Project,
    Task,
    TaskDependency,
    TaskRun,
    WorkerInstance,
    WorkerRuntime,
)


@pytest.fixture(scope="function")
async def dbsession():
    """In-memory SQLite session for isolated tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# 17 entity import / instantiation tests
# ---------------------------------------------------------------------------


ALL_MODELS = [
    Project,
    Task,
    TaskDependency,
    TaskRun,
    WorkerRuntime,
    WorkerInstance,
    Artifact,
    Issue,
    Decision,
    Constraint,
    GateResult,
    ManualAction,
    HumanDecision,
    Event,
    Checkpoint,
    DeliveryContract,
    DeliveryItem,
]


@pytest.mark.parametrize("model_class", ALL_MODELS)
def test_model_importable(model_class):
    """Every required entity is importable."""
    assert model_class is not None
    assert hasattr(model_class, "__tablename__")


# ---------------------------------------------------------------------------
# Repository CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_crud(dbsession: AsyncSession):
    repo = BaseRepository[Project](Project, dbsession)

    p = Project(name="test", automation_mode=AutomationMode.FULL_AUTO)
    await repo.add(p)
    assert p.id is not None

    fetched = await repo.get(p.id)
    assert fetched is not None
    assert fetched.name == "test"
    assert fetched.automation_mode == AutomationMode.FULL_AUTO
    assert fetched.status == ProjectStatus.CREATED


@pytest.mark.asyncio
async def test_task_with_project(dbsession: AsyncSession):
    proj_repo = BaseRepository[Project](Project, dbsession)
    task_repo = BaseRepository[Task](Task, dbsession)

    p = Project(name="demo", automation_mode=AutomationMode.MANUAL)
    await proj_repo.add(p)

    t = Task(project_id=p.id, name="build-api", task_type="coding")
    await task_repo.add(t)
    assert t.id is not None

    fetched = await task_repo.get(t.id)
    assert fetched is not None
    assert fetched.project_id == p.id
    assert fetched.name == "build-api"


@pytest.mark.asyncio
async def test_task_dependency(dbsession: AsyncSession):
    proj_repo = BaseRepository[Project](Project, dbsession)
    task_repo = BaseRepository[Task](Task, dbsession)

    p = Project(name="dep-test")
    await proj_repo.add(p)

    t1 = Task(project_id=p.id, name="design")
    t2 = Task(project_id=p.id, name="implement")
    await task_repo.add(t1)
    await task_repo.add(t2)

    dep = TaskDependency(task_id=t2.id, depends_on_id=t1.id, dependency_type=DependencyType.HARD)
    dbsession.add(dep)
    await dbsession.flush()
    assert dep.id is not None
    assert dep.dependency_type == DependencyType.HARD


@pytest.mark.asyncio
async def test_all_models_persist(dbsession: AsyncSession):
    """Smoke test: every entity can be created and persisted."""
    proj_repo = BaseRepository[Project](Project, dbsession)

    p = Project(name="full")
    await proj_repo.add(p)

    # 2. Task
    t = Task(project_id=p.id, name="t1")
    dbsession.add(t)
    await dbsession.flush()

    # 3. TaskDependency
    dbsession.add(TaskDependency(task_id=t.id, depends_on_id=t.id))
    # 4. TaskRun
    dbsession.add(TaskRun(task_id=t.id))
    # 5. WorkerRuntime
    wr = WorkerRuntime(worker_type="goose")
    dbsession.add(wr)
    await dbsession.flush()

    tr = TaskRun(task_id=t.id)
    dbsession.add(tr)
    await dbsession.flush()

    # 6. WorkerInstance
    dbsession.add(WorkerInstance(worker_runtime_id=wr.id, task_run_id=tr.id))
    # 7. Artifact
    dbsession.add(
        Artifact(
            logical_artifact_id="a1",
            project_id=p.id,
            task_id=t.id,
            name="out.txt",
            path="/tmp/out.txt",
        )
    )
    # 8. Issue
    dbsession.add(Issue(project_id=p.id, task_id=t.id, title="bug"))
    # 9. Decision
    dbsession.add(Decision(project_id=p.id, title="use-sqlite"))
    # 10. Constraint
    dbsession.add(Constraint(project_id=p.id, value="mem<=512MB"))
    # 11. GateResult
    dbsession.add(GateResult(task_id=t.id, gate_name="lint"))
    # 12. ManualAction
    dbsession.add(ManualAction(project_id=p.id, task_id=t.id, action_type="TASK_DISPATCH"))
    # 13. HumanDecision
    dbsession.add(HumanDecision(project_id=p.id, decision="approved"))
    # 14. Event
    dbsession.add(Event(project_id=p.id, task_id=t.id, event_type="TASK_CREATED"))
    # 15. Checkpoint
    dbsession.add(Checkpoint(project_id=p.id, snapshot="{}"))
    # 16. DeliveryContract
    dc = DeliveryContract(project_id=p.id)
    dbsession.add(dc)
    await dbsession.flush()

    # 17. DeliveryItem
    dbsession.add(DeliveryItem(contract_id=dc.id, requirement_ref="REQ-001"))

    await dbsession.flush()


@pytest.mark.asyncio
async def test_list_pagination(dbsession: AsyncSession):
    repo = BaseRepository[Project](Project, dbsession)
    for i in range(5):
        await repo.add(Project(name=f"p{i}"))

    items = await repo.list(limit=3, offset=0)
    assert len(items) == 3
    items_page2 = await repo.list(limit=3, offset=3)
    assert len(items_page2) == 2


@pytest.mark.asyncio
async def test_delete(dbsession: AsyncSession):
    repo = BaseRepository[Project](Project, dbsession)
    p = Project(name="to-delete")
    await repo.add(p)
    pid = p.id

    await repo.delete(p)
    assert await repo.get(pid) is None
