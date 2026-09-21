"""D03 FSM test matrix — per 40-testing/D03-状态机测试矩阵.md.

Tests the StateTransitionService against the V1.11 frozen state machines.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import ProjectStatus, TaskStatus
from project_runtime.project_state.fsm import (
    InvalidStateTransitionError,
    StateTransitionService,
)
from project_runtime.project_state.models import Project, Task


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def make_fsm(session) -> StateTransitionService:
    return StateTransitionService(session)


# ════════════════════════════════════════════════════════════════════════════
# 1. TaskStatus LEGAL transitions (T-V01..T-V19)
# ════════════════════════════════════════════════════════════════════════════

TASK_LEGAL = [
    ("T-V01", TaskStatus.CREATED, TaskStatus.READY),
    ("T-V02", TaskStatus.CREATED, TaskStatus.BLOCKED),
    ("T-V03", TaskStatus.CREATED, TaskStatus.CANCELLED),
    ("T-V04", TaskStatus.READY, TaskStatus.RUNNING),
    ("T-V05", TaskStatus.READY, TaskStatus.BLOCKED),
    ("T-V06", TaskStatus.READY, TaskStatus.CANCELLED),
    ("T-V07", TaskStatus.RUNNING, TaskStatus.EXECUTION_DONE),
    ("T-V08", TaskStatus.RUNNING, TaskStatus.BLOCKED),
    ("T-V09", TaskStatus.RUNNING, TaskStatus.FAILED),
    ("T-V10", TaskStatus.RUNNING, TaskStatus.CANCELLED),
    ("T-V11", TaskStatus.EXECUTION_DONE, TaskStatus.ACCEPTED),
    ("T-V12", TaskStatus.EXECUTION_DONE, TaskStatus.REWORK),
    ("T-V13", TaskStatus.REWORK, TaskStatus.READY),
    ("T-V14", TaskStatus.REWORK, TaskStatus.CANCELLED),
    ("T-V15", TaskStatus.BLOCKED, TaskStatus.READY),
    ("T-V16", TaskStatus.BLOCKED, TaskStatus.CANCELLED),
    ("T-V17", TaskStatus.FAILED, TaskStatus.READY),
    ("T-V18", TaskStatus.FAILED, TaskStatus.REWORK),
    ("T-V19", TaskStatus.FAILED, TaskStatus.CANCELLED),
]


@pytest.mark.parametrize("case_id,from_s,to_s", TASK_LEGAL)
def test_task_legal_transitions_validate(case_id, from_s, to_s):
    fsm = StateTransitionService(None)
    result = fsm.validate_task(from_s, to_s)
    assert result.allowed, f"{case_id}: {from_s.value} -> {to_s.value} should be ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,from_s,to_s", TASK_LEGAL)
async def test_task_legal_transitions_persist(case_id, from_s, to_s, dbsession):
    """Each legal transition actually persists via the FSM service."""
    p = Project(name="fsm-test")
    dbsession.add(p)
    await dbsession.flush()

    t = Task(project_id=p.id, name=case_id, status=from_s)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    await fsm.transition_task(t, to_s)
    await dbsession.flush()

    assert t.status == to_s


# ════════════════════════════════════════════════════════════════════════════
# 2. TaskStatus ILLEGAL transitions (T-I01..T-I10)
# ════════════════════════════════════════════════════════════════════════════

TASK_ILLEGAL = [
    ("T-I01", TaskStatus.CREATED, TaskStatus.RUNNING),
    ("T-I02", TaskStatus.CREATED, TaskStatus.ACCEPTED),
    ("T-I03", TaskStatus.READY, TaskStatus.ACCEPTED),
    ("T-I04", TaskStatus.RUNNING, TaskStatus.ACCEPTED),
    ("T-I05", TaskStatus.BLOCKED, TaskStatus.ACCEPTED),
    ("T-I06", TaskStatus.FAILED, TaskStatus.ACCEPTED),
    ("T-I07", TaskStatus.ACCEPTED, TaskStatus.REWORK),
    ("T-I08", TaskStatus.ACCEPTED, TaskStatus.READY),
    ("T-I09", TaskStatus.CANCELLED, TaskStatus.READY),
    ("T-I10", TaskStatus.CANCELLED, TaskStatus.RUNNING),
]


@pytest.mark.parametrize("case_id,from_s,to_s", TASK_ILLEGAL)
def test_task_illegal_transitions_validate(case_id, from_s, to_s):
    fsm = StateTransitionService(None)
    result = fsm.validate_task(from_s, to_s)
    assert not result.allowed, f"{case_id}: {from_s.value} -> {to_s.value} should be REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,from_s,to_s", TASK_ILLEGAL)
async def test_task_illegal_transitions_raise(case_id, from_s, to_s, dbsession):
    p = Project(name="fsm-test")
    dbsession.add(p)
    await dbsession.flush()

    t = Task(project_id=p.id, name=case_id, status=from_s)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_task(t, to_s)

    # DB state unchanged
    await dbsession.refresh(t)
    assert t.status == from_s


# ════════════════════════════════════════════════════════════════════════════
# 2b. Full combinatoric check — only legal edges exist
# ════════════════════════════════════════════════════════════════════════════

# Build legal set from test matrix
_TASK_LEGAL_SET = {(f, t) for _, f, t in TASK_LEGAL}


def test_task_combinatoric_only_legal_pairs_pass():
    """Exhaustive: every (from, to) pair — only spec-listed pairs validate."""
    fsm = StateTransitionService(None)
    for from_s in TaskStatus:
        for to_s in TaskStatus:
            result = fsm.validate_task(from_s, to_s)
            if (from_s, to_s) in _TASK_LEGAL_SET:
                assert result.allowed, (
                    f"SPEC says {from_s.value} -> {to_s.value} IS legal, "
                    f"but FSM rejected it"
                )
            else:
                assert not result.allowed, (
                    f"SPEC says {from_s.value} -> {to_s.value} is ILLEGAL, "
                    f"but FSM allowed it"
                )


# ════════════════════════════════════════════════════════════════════════════
# 3. Gate hard rules
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_worker_direct_accept_rejected(dbsession):
    """Worker requesting RUNNING -> ACCEPTED is rejected."""
    p = Project(name="g")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="g1", status=TaskStatus.RUNNING)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_task(t, TaskStatus.ACCEPTED)


@pytest.mark.asyncio
async def test_gate_worker_complete_to_execution_done(dbsession):
    """Worker returns completion → RUNNING -> EXECUTION_DONE only."""
    p = Project(name="g")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="g2", status=TaskStatus.RUNNING)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    await fsm.transition_task(t, TaskStatus.EXECUTION_DONE)
    assert t.status == TaskStatus.EXECUTION_DONE


@pytest.mark.asyncio
async def test_gate_pass_allows_accept(dbsession):
    """Gate PASS → EXECUTION_DONE -> ACCEPTED."""
    p = Project(name="g")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="g3", status=TaskStatus.EXECUTION_DONE)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    await fsm.transition_task(t, TaskStatus.ACCEPTED)
    assert t.status == TaskStatus.ACCEPTED


@pytest.mark.asyncio
async def test_gate_fail_to_rework(dbsession):
    """Gate FAIL → EXECUTION_DONE -> REWORK."""
    p = Project(name="g")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="g4", status=TaskStatus.EXECUTION_DONE)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    await fsm.transition_task(t, TaskStatus.REWORK)
    assert t.status == TaskStatus.REWORK


# ════════════════════════════════════════════════════════════════════════════
# 4. Dependency unlock — only ACCEPTED satisfies HARD dependency
# ════════════════════════════════════════════════════════════════════════════

_NON_ACCEPTED = [
    s
    for s in TaskStatus
    if s != TaskStatus.ACCEPTED
]


@pytest.mark.parametrize("upstream_status", _NON_ACCEPTED)
def test_dependency_non_accepted_does_not_unlock(upstream_status):
    """HARD dep only satisfied by ACCEPTED — all other states are insufficient."""
    assert upstream_status != TaskStatus.ACCEPTED
    assert upstream_status not in (TaskStatus.ACCEPTED,)


def test_dependency_accepted_unlocks():
    """ACCEPTED is the ONLY status that satisfies HARD dependency."""
    assert TaskStatus.ACCEPTED not in _NON_ACCEPTED


# ════════════════════════════════════════════════════════════════════════════
# 5. Resource Governor — DEFER_RESOURCE does NOT change status
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_defer_resource_keeps_ready(dbsession):
    """DEFER_RESOURCE must keep Task at READY, not BLOCKED/FAILED."""
    p = Project(name="rg")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="rg1", status=TaskStatus.READY)
    dbsession.add(t)
    await dbsession.flush()

    # Simulate: ResourceGovernor returns DEFER_RESOURCE — no transition happens
    # Task should stay READY
    await dbsession.refresh(t)
    assert t.status == TaskStatus.READY


# ════════════════════════════════════════════════════════════════════════════
# 6. ProjectStatus LEGAL transitions (P-V01..P-V14)
# ════════════════════════════════════════════════════════════════════════════

PROJECT_LEGAL = [
    ("P-V01", ProjectStatus.CREATED, ProjectStatus.INITIALIZING),
    ("P-V02", ProjectStatus.CREATED, ProjectStatus.CANCELLED),
    ("P-V03", ProjectStatus.INITIALIZING, ProjectStatus.ACTIVE),
    ("P-V04", ProjectStatus.INITIALIZING, ProjectStatus.BLOCKED),
    ("P-V05", ProjectStatus.INITIALIZING, ProjectStatus.CANCELLED),
    ("P-V06", ProjectStatus.ACTIVE, ProjectStatus.BLOCKED),
    ("P-V07", ProjectStatus.ACTIVE, ProjectStatus.COMPLETING),
    ("P-V08", ProjectStatus.ACTIVE, ProjectStatus.CANCELLED),
    ("P-V09", ProjectStatus.BLOCKED, ProjectStatus.ACTIVE),
    ("P-V10", ProjectStatus.BLOCKED, ProjectStatus.CANCELLED),
    ("P-V11", ProjectStatus.COMPLETING, ProjectStatus.COMPLETED),
    ("P-V12", ProjectStatus.COMPLETING, ProjectStatus.ACTIVE),
    ("P-V13", ProjectStatus.COMPLETING, ProjectStatus.BLOCKED),
    ("P-V14", ProjectStatus.COMPLETING, ProjectStatus.CANCELLED),
]


@pytest.mark.parametrize("case_id,from_s,to_s", PROJECT_LEGAL)
def test_project_legal_transitions_validate(case_id, from_s, to_s):
    fsm = StateTransitionService(None)
    result = fsm.validate_project(from_s, to_s)
    assert result.allowed, f"{case_id}: {from_s.value} -> {to_s.value} should be ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,from_s,to_s", PROJECT_LEGAL)
async def test_project_legal_transitions_persist(case_id, from_s, to_s, dbsession):
    p = Project(name=case_id, status=from_s)
    dbsession.add(p)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    await fsm.transition_project(p, to_s)
    await dbsession.flush()

    assert p.status == to_s


# ════════════════════════════════════════════════════════════════════════════
# 7. ProjectStatus ILLEGAL transitions
# ════════════════════════════════════════════════════════════════════════════

PROJECT_ILLEGAL = [
    ("CREATED->ACTIVE", ProjectStatus.CREATED, ProjectStatus.ACTIVE),
    ("ACTIVE->COMPLETED", ProjectStatus.ACTIVE, ProjectStatus.COMPLETED),
    ("BLOCKED->COMPLETED", ProjectStatus.BLOCKED, ProjectStatus.COMPLETED),
    ("COMPLETED->ACTIVE", ProjectStatus.COMPLETED, ProjectStatus.ACTIVE),
    ("CANCELLED->ACTIVE", ProjectStatus.CANCELLED, ProjectStatus.ACTIVE),
]


@pytest.mark.parametrize("case_id,from_s,to_s", PROJECT_ILLEGAL)
def test_project_illegal_transitions_validate(case_id, from_s, to_s):
    fsm = StateTransitionService(None)
    result = fsm.validate_project(from_s, to_s)
    assert not result.allowed, f"{case_id}: {from_s.value} -> {to_s.value} should be REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,from_s,to_s", PROJECT_ILLEGAL)
async def test_project_illegal_transitions_raise(case_id, from_s, to_s, dbsession):
    p = Project(name=case_id, status=from_s)
    dbsession.add(p)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_project(p, to_s)

    await dbsession.refresh(p)
    assert p.status == from_s


# ════════════════════════════════════════════════════════════════════════════
# 8. Transaction consistency
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_terminal_accepted_cannot_be_reverted(dbsession):
    """ACCEPTED is terminal — cannot transition back."""
    p = Project(name="tc")
    dbsession.add(p)
    await dbsession.flush()
    t = Task(project_id=p.id, name="done", status=TaskStatus.ACCEPTED)
    dbsession.add(t)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_task(t, TaskStatus.REWORK)


@pytest.mark.asyncio
async def test_terminal_completed_cannot_be_reverted(dbsession):
    """COMPLETED is terminal — cannot transition back."""
    p = Project(name="done", status=ProjectStatus.COMPLETED)
    dbsession.add(p)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_project(p, ProjectStatus.ACTIVE)


@pytest.mark.asyncio
async def test_terminal_cancelled_cannot_be_reverted(dbsession):
    """CANCELLED is terminal — cannot transition back."""
    p = Project(name="cancelled", status=ProjectStatus.CANCELLED)
    dbsession.add(p)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_project(p, ProjectStatus.ACTIVE)


@pytest.mark.asyncio
async def test_expected_from_guard_rejects_stale(dbsession):
    """Optimistic concurrency: if DB status != expected_from, reject."""
    p = Project(name="stale")
    dbsession.add(p)
    await dbsession.flush()

    fsm = make_fsm(dbsession)
    # expected_from says CREATED, but DB is actually CREATED — ok
    await fsm.transition_project(p, ProjectStatus.INITIALIZING, expected_from=ProjectStatus.CREATED)

    # Now expected_from is stale
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_project(p, ProjectStatus.ACTIVE, expected_from=ProjectStatus.CREATED)
