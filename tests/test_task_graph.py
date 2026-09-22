"""D04 Task Graph — tests for CRUD, dependencies, cycle detection, and graph validation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import DependencyType, TaskStatus
from project_runtime.project_state.fsm import InvalidStateTransitionError
from project_runtime.project_state.models import Project
from project_runtime.task_graph.graph import topological_order
from project_runtime.task_graph.manager import (
    CreateTaskRequest,
    TaskGraphManager,
    UpdateTaskRequest,
)


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def mgr(dbsession: AsyncSession) -> TaskGraphManager:
    return TaskGraphManager(dbsession)


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d04-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# CRUD
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_task(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="design"))
    assert t.id is not None
    assert t.name == "design"
    assert t.status == TaskStatus.CREATED


@pytest.mark.asyncio
async def test_get_task(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="t1"))
    fetched = await mgr.get_task(t.id)
    assert fetched is not None
    assert fetched.name == "t1"


@pytest.mark.asyncio
async def test_list_tasks(mgr, project):
    await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    tasks = await mgr.list_tasks(project.id)
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_update_task_metadata(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="old"))
    updated = await mgr.update_task(t.id, UpdateTaskRequest(name="new", contract="do stuff"))
    assert updated.name == "new"
    assert updated.contract == "do stuff"
    assert updated.status == TaskStatus.CREATED  # status unchanged


@pytest.mark.asyncio
async def test_cancel_task(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="cancel-me"))
    cancelled = await mgr.cancel_task(t.id)
    assert cancelled.status == TaskStatus.CANCELLED


# ════════════════════════════════════════════════════════════════════════════
# Dependencies
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_dependency(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="up"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="down"))
    dep = await mgr.add_dependency(t2.id, t1.id)
    assert dep.task_id == t2.id
    assert dep.depends_on_id == t1.id
    assert dep.dependency_type == DependencyType.HARD


@pytest.mark.asyncio
async def test_self_dependency_rejected(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="self"))
    with pytest.raises(ValueError, match="cannot depend on itself"):
        await mgr.add_dependency(t.id, t.id)


@pytest.mark.asyncio
async def test_duplicate_dependency_rejected(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="u"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="d"))
    await mgr.add_dependency(t2.id, t1.id)
    with pytest.raises(ValueError, match="already exists"):
        await mgr.add_dependency(t2.id, t1.id)


@pytest.mark.asyncio
async def test_remove_dependency(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    await mgr.add_dependency(t2.id, t1.id)
    await mgr.remove_dependency(t2.id, t1.id)
    deps = await mgr.get_dependencies(t2.id)
    assert len(deps) == 0


@pytest.mark.asyncio
async def test_get_dependencies(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    t3 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="c"))
    await mgr.add_dependency(t3.id, t1.id)
    await mgr.add_dependency(t3.id, t2.id)
    deps = await mgr.get_dependencies(t3.id)
    assert len(deps) == 2


@pytest.mark.asyncio
async def test_get_dependents(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="lib"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="app1"))
    t3 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="app2"))
    await mgr.add_dependency(t2.id, t1.id)
    await mgr.add_dependency(t3.id, t1.id)
    dependents = await mgr.get_dependents(t1.id)
    assert len(dependents) == 2


# ════════════════════════════════════════════════════════════════════════════
# Cycle detection
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_simple_cycle_rejected(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    await mgr.add_dependency(t2.id, t1.id)
    with pytest.raises(ValueError, match="Cycle"):
        await mgr.add_dependency(t1.id, t2.id)


@pytest.mark.asyncio
async def test_three_node_cycle_rejected(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    t3 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="c"))
    await mgr.add_dependency(t2.id, t1.id)
    await mgr.add_dependency(t3.id, t2.id)
    with pytest.raises(ValueError, match="Cycle"):
        await mgr.add_dependency(t1.id, t3.id)


@pytest.mark.asyncio
async def test_dag_accepted(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="root"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="mid"))
    t3 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="leaf"))
    await mgr.add_dependency(t2.id, t1.id)
    await mgr.add_dependency(t3.id, t2.id)
    await mgr.add_dependency(t3.id, t1.id)
    # no exception = pass


# ════════════════════════════════════════════════════════════════════════════
# HARD dependency: only ACCEPTED unlocks
# ════════════════════════════════════════════════════════════════════════════


async def _make_task(mgr, project, name, status):
    """Create a task with the given status via direct DB insert (test setup only)."""
    from project_runtime.project_state.models import Task as TaskModel

    t = TaskModel(
        project_id=project.id, name=name, status=status, task_type="test"
    )
    mgr._session.add(t)
    await mgr._session.flush()
    return t


@pytest.mark.asyncio
async def test_hard_dep_only_accepted_satisfies(mgr, project):
    upstream = await _make_task(mgr, project, "up", TaskStatus.CREATED)
    downstream = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="down"))
    await mgr.add_dependency(downstream.id, upstream.id)

    # CREATED → cannot set READY
    assert not await mgr.can_set_ready(downstream.id)

    # EXECUTION_DONE → still cannot set READY
    await mgr.transition_task(upstream.id, TaskStatus.READY)
    await mgr.transition_task(upstream.id, TaskStatus.RUNNING)
    await mgr.transition_task(upstream.id, TaskStatus.EXECUTION_DONE)
    assert not await mgr.can_set_ready(downstream.id)

    # Only ACCEPTED (actor guard: EXECUTION_DONE→ACCEPTED needs GATE actor)
    await mgr.transition_task(upstream.id, TaskStatus.ACCEPTED, actor="GATE")
    assert await mgr.can_set_ready(downstream.id)


@pytest.mark.parametrize("non_accepted", [
    TaskStatus.CREATED,
    TaskStatus.READY,
    TaskStatus.RUNNING,
    TaskStatus.EXECUTION_DONE,
    TaskStatus.REWORK,
    TaskStatus.BLOCKED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
])
@pytest.mark.asyncio
async def test_non_accepted_never_unlocks(mgr, project, non_accepted):
    upstream = await _make_task(mgr, project, "up", non_accepted)
    downstream = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="down"))
    await mgr.add_dependency(downstream.id, upstream.id)
    assert not await mgr.can_set_ready(downstream.id)


# ════════════════════════════════════════════════════════════════════════════
# promote_to_ready
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_promote_to_ready_with_satisfied_deps(mgr, project):
    upstream = await _make_task(mgr, project, "up", TaskStatus.ACCEPTED)
    downstream = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="down"))
    await mgr.add_dependency(downstream.id, upstream.id)

    t = await mgr.promote_to_ready(downstream.id)
    assert t.status == TaskStatus.READY


@pytest.mark.asyncio
async def test_promote_to_ready_blocked_by_dep(mgr, project):
    upstream = await _make_task(mgr, project, "up", TaskStatus.CREATED)
    downstream = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="down"))
    await mgr.add_dependency(downstream.id, upstream.id)

    with pytest.raises(ValueError, match="HARD dependencies not satisfied"):
        await mgr.promote_to_ready(downstream.id)


@pytest.mark.asyncio
async def test_promote_to_ready_no_deps(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="root"))
    promoted = await mgr.promote_to_ready(t.id)
    assert promoted.status == TaskStatus.READY


# ════════════════════════════════════════════════════════════════════════════
# State transitions via FSM
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_transition_via_manager(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="t"))
    t = await mgr.transition_task(t.id, TaskStatus.READY)
    assert t.status == TaskStatus.READY


@pytest.mark.asyncio
async def test_illegal_transition_rejected_via_manager(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="t"))
    # CREATED -> ACCEPTED is illegal
    with pytest.raises(InvalidStateTransitionError):
        await mgr.transition_task(t.id, TaskStatus.ACCEPTED)
    # DB unchanged
    t2 = await mgr.get_task(t.id)
    assert t2.status == TaskStatus.CREATED


@pytest.mark.asyncio
async def test_cancelled_cannot_be_reverted(mgr, project):
    t = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="t"))
    await mgr.cancel_task(t.id)
    with pytest.raises(InvalidStateTransitionError):
        await mgr.transition_task(t.id, TaskStatus.READY)


# ════════════════════════════════════════════════════════════════════════════
# Graph invariants
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_graph_invariants_clean_dag(mgr, project):
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="root"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="mid"))
    t3 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="leaf"))
    await mgr.add_dependency(t2.id, t1.id)
    await mgr.add_dependency(t3.id, t2.id)
    issues = await mgr.check_graph_invariants(project.id)
    assert len(issues) == 0


@pytest.mark.asyncio
async def test_graph_invariants_single_task(mgr, project):
    await mgr.create_task(CreateTaskRequest(project_id=project.id, name="solo"))
    issues = await mgr.check_graph_invariants(project.id)
    assert len(issues) == 0  # single task is both root and leaf


@pytest.mark.asyncio
async def test_graph_invariants_all_hard_dep_no_root(mgr, project):
    """If every task has a HARD dep, there must be cycles — detected by cycle check."""
    # Two tasks depending on each other = cycle, rejected at add_dependency
    t1 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="a"))
    t2 = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="b"))
    with pytest.raises(ValueError, match="Cycle"):
        await mgr.add_dependency(t1.id, t2.id)
        await mgr.add_dependency(t2.id, t1.id)


# ════════════════════════════════════════════════════════════════════════════
# topological_order (D14 Scheduler 预留)
# ════════════════════════════════════════════════════════════════════════════


def test_topological_order_dag():
    edges = [(2, 1), (3, 1), (3, 2)]
    result = topological_order(edges)
    assert len(result) == 3
    assert result.index(1) > result.index(2)
    assert result.index(1) > result.index(3)
    assert result.index(2) > result.index(3)


def test_topological_order_cycle():
    edges = [(1, 2), (2, 1)]
    with pytest.raises(ValueError, match="Cycle"):
        topological_order(edges)


def test_topological_order_empty():
    assert topological_order([]) == []


def test_topological_order_single_node():
    edges: list = []
    assert topological_order(edges) == []
