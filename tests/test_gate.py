"""D13 Gate — tests for GateCore pipeline, validators, and FSM enforcement."""

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401 — fixture 建表需注册
from project_runtime.artifact.manager import ArtifactManager
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import GateStatus, TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Project, Task
from project_runtime.reviewer.gate import (
    BuildValidator,
    FileValidator,
    GateCore,
    TestValidator,
    ValidatorResult,
)
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.adapter import MockWorkerAdapter, WorkPackage


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
    p = Project(name="d13-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _executed_task(dbsession, project, with_artifact: bool = True) -> Task:
    """Create a task and run it to EXECUTION_DONE via MockWorker."""
    mgr = TaskGraphManager(dbsession)
    task = Task(project_id=project.id, name="gate-task")
    task = await mgr.create_task(task)
    await mgr.promote_to_ready(task.id)

    artifact_mgr = (
        ArtifactManager(dbsession, workspace_root="data/test_gate") if with_artifact else None
    )
    worker = MockWorkerAdapter(dbsession, artifact_mgr=artifact_mgr)
    await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))
    await dbsession.refresh(task)
    assert task.status == TaskStatus.EXECUTION_DONE
    return task


# ════════════════════════════════════════════════════════════════════════════
# Gate PASS path
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_pass_transitions_to_accepted(dbsession, project):
    task = await _executed_task(dbsession, project, with_artifact=True)

    gate = GateCore(dbsession)
    result = await gate.evaluate(task.id)

    assert result.status == GateStatus.PASS
    await dbsession.refresh(task)
    assert task.status == TaskStatus.ACCEPTED


# ════════════════════════════════════════════════════════════════════════════
# Gate FAIL path — no artifacts
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_fail_no_artifacts(dbsession, project):
    task = await _executed_task(dbsession, project, with_artifact=False)

    gate = GateCore(dbsession)
    result = await gate.evaluate(task.id)

    assert result.status == GateStatus.FAIL
    await dbsession.refresh(task)
    assert task.status == TaskStatus.REWORK


# ════════════════════════════════════════════════════════════════════════════
# Gate FAIL path — sha256 mismatch
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_fail_sha256_mismatch(dbsession, project):
    task = await _executed_task(dbsession, project, with_artifact=True)

    # Corrupt the artifact hash
    from sqlalchemy import select

    from project_runtime.project_state.models import Artifact

    stmt = select(Artifact).where(Artifact.task_id == task.id)
    artifact = (await dbsession.execute(stmt)).scalar_one()
    artifact.sha256 = "0" * 64
    await dbsession.flush()

    gate = GateCore(dbsession)
    result = await gate.evaluate(task.id)
    assert result.status == GateStatus.FAIL
    assert "sha256 mismatch" in result.details


# ════════════════════════════════════════════════════════════════════════════
# Gate requires EXECUTION_DONE
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_rejects_non_execution_done(dbsession, project):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="not-done"))

    gate = GateCore(dbsession)
    with pytest.raises(ValueError, match="EXECUTION_DONE"):
        await gate.evaluate(task.id)


# ════════════════════════════════════════════════════════════════════════════
# GateResult persisted
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_result_persisted(dbsession, project):
    task = await _executed_task(dbsession, project, with_artifact=True)

    gate = GateCore(dbsession)
    result = await gate.evaluate(task.id)

    assert result.id is not None
    assert result.task_id == task.id
    assert result.gate_name == "gate_core"
    assert "[file]" in result.details


# ════════════════════════════════════════════════════════════════════════════
# Validator interface
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_validators_return_only_pass_or_fail(dbsession, project):
    task = await _executed_task(dbsession, project, with_artifact=True)
    from sqlalchemy import select

    from project_runtime.project_state.models import Artifact

    stmt = select(Artifact).where(Artifact.task_id == task.id)
    artifacts = list((await dbsession.execute(stmt)).scalars().all())

    for v in (FileValidator(), BuildValidator(), TestValidator()):
        r: ValidatorResult = await v.validate(task, artifacts)
        assert r.status in (GateStatus.PASS, GateStatus.FAIL)
        # Validators must not change task status
        await dbsession.refresh(task)
        assert task.status == TaskStatus.EXECUTION_DONE


# ════════════════════════════════════════════════════════════════════════════
# Red line: EXECUTION_DONE → ACCEPTED only via Gate
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execution_done_to_accepted_only_via_gate(dbsession, project):
    """Actor guard (D11-TD01): EXECUTION_DONE→ACCEPTED only allowed for actor='GATE'."""
    task = await _executed_task(dbsession, project, with_artifact=True)

    # Non-GATE actor is rejected by FSM actor guard
    fsm = StateTransitionService(dbsession)
    from project_runtime.project_state.fsm import InvalidStateTransitionError
    with pytest.raises(InvalidStateTransitionError):
        await fsm.transition_task(task, TaskStatus.ACCEPTED, actor="WORKER")
    await dbsession.refresh(task)
    assert task.status == TaskStatus.EXECUTION_DONE  # unchanged

    # Gate is the supported path and works
    gate = GateCore(dbsession)
    await gate.evaluate(task.id)
    await dbsession.refresh(task)
    assert task.status == TaskStatus.ACCEPTED

    # ACCEPTED is terminal — no further transitions
    with pytest.raises(Exception):
        await fsm.transition_task(task, TaskStatus.RUNNING, actor="GATE")


# ═══ 返修单-REWORK轮sha256误拦 专项 ═════════════════════════════

class TestReworkHashRefresh:
    """REWORK 链重写文件 → WARNING 放行 + 登记刷新; 首轮篡改仍 FAIL。"""

    @staticmethod
    def _mk_artifact(tmp_path, name="src/A.java", content="v1",
                      sha=None, ws="data/tasks/9/1"):
        import hashlib

        ws_dir = tmp_path / ws
        f = ws_dir / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        path = str(f)

        class _A:
            pass

        a = _A()
        a.path = path
        a.sha256 = sha or hashlib.sha256(
            content.encode()).hexdigest()
        a.name = name.split("/")[-1]
        return a

    @pytest.mark.asyncio
    async def test_rework_rewrite_warns(self, tmp_path, monkeypatch):
        """REWORK 链: 磁盘重写（hash 变）→ WARNING + 工单, 非 FAIL。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.reviewer.gate import (
            FileValidator,
            GateStatus,
        )

        # 登记 hash = v1, 磁盘已重写为 v2
        a = self._mk_artifact(tmp_path, content="version-2-rewritten",
                              sha=hashlib.sha256(b"version-1").hexdigest())

        class _T:
            _gate_fail_history = True  # GateCore 注入的 REWORK 链标记
            name = "任务X"

        r = await FileValidator().validate(_T(), [a])
        assert r.status == GateStatus.WARNING, r.message
        assert "REFRESH:" in r.message

    @pytest.mark.asyncio
    async def test_first_tamper_still_fails(self, tmp_path, monkeypatch):
        """首轮（无 FAIL 史）: 磁盘≠登记 → 仍 FAIL（防篡改不回退）。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.reviewer.gate import FileValidator, GateStatus

        a = self._mk_artifact(tmp_path, content="tampered",
                               sha=hashlib.sha256(b"original").hexdigest())

        class _T:
            _gate_fail_history = False
            name = "任务Y"

        r = await FileValidator().validate(_T(), [a])
        assert r.status == GateStatus.FAIL
        assert "sha256 mismatch" in r.message

    @pytest.mark.asyncio
    async def test_matching_hashes_pass(self, tmp_path, monkeypatch):
        """hash 一致（含 REWORK 链）→ PASS。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.reviewer.gate import FileValidator, GateStatus

        a = self._mk_artifact(tmp_path, content="same")

        class _T:
            _gate_fail_history = True
            name = "任务Z"

        r = await FileValidator().validate(_T(), [a])
        assert r.status == GateStatus.PASS

    def test_relpath_strips_prefix(self, tmp_path, monkeypatch):
        """路径相对化: 绝对路径 → data/tasks 下相对（防 details 截断）。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.reviewer.gate import _relpath

        p = str(tmp_path / "data/tasks/2/15/src/main/X.java")
        out = _relpath(p)
        assert "X.java" in out and "G:" not in out
