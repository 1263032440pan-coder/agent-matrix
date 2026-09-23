"""Runtime Hardening E2E — 测试矩阵 E/F/D 组（RH 系列总验收）.

E 组（全链）: POST /api/executions → 202 → QUEUED → RUNNING → Worker →
              Artifact → Gate → Task EXECUTION_DONE → Gate PASS → ACCEPTED
F 组（Fail Gate 负断言）: SUCCEEDED 不直接 ACCEPTED；刷新后可查；
              HTTP 不等待整个执行；事件持久可追溯
D 组（并发）: 3 Execution 并发全链无持续锁

走真实 API 层（httpx ASGI transport）+ 真实 ExecutionService 后台 runner。
LLM mock 在 Worker 边界（_execute_llm）。
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.engine as event_engine_mod
import project_runtime.execution.service as exec_service_mod

# apps import pulls FastAPI app + production engine; patch AFTER import
from apps.runtime.main import app  # noqa: E402
from project_runtime.event import EventEngine
from project_runtime.execution.models import Execution, ExecutionStatus
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, TaskStatus
from project_runtime.project_state.models import Artifact, Project, Task
from project_runtime.task_graph.manager import TaskGraphManager


@pytest.fixture(scope="function")
async def rh_env(tmp_path, monkeypatch):
    """Test DB + patched engines (execution service, event engine, API layer)."""
    db = tmp_path / "rh_e2e.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db}", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(exec_service_mod, "engine", test_engine)
    monkeypatch.setattr(event_engine_mod, "engine", test_engine)

    # API layer engines (executions + projects routers import engine directly)
    import apps.runtime.api.executions as api_exec
    import apps.runtime.api.projects as api_proj
    monkeypatch.setattr(api_exec, "engine", test_engine)
    monkeypatch.setattr(api_proj, "engine", test_engine)

    yield test_engine
    await test_engine.dispose()


async def _seed_ready_task(engine, name="rh-task") -> tuple[int, int]:
    async with AsyncSession(engine) as s:
        p = Project(name="rh-e2e", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(Task(project_id=pid, name=name))
        await mgr.promote_to_ready(t.id)
        tid = t.id
        await s.commit()
        return pid, tid


def _mock_llm(monkeypatch, output="# RH-E2E 产出\n完整交付内容。"):
    async def fake_llm(self, wp):
        return output

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )


async def _wait_terminal(engine, execution_id: int, timeout_s: float = 15.0) -> str:
    for _ in range(int(timeout_s / 0.1)):
        async with AsyncSession(engine) as s:
            ex = await s.get(Execution, execution_id)
            if ex and ex.status not in (
                ExecutionStatus.QUEUED.value, ExecutionStatus.RUNNING.value,
            ):
                return ex.status
        await asyncio.sleep(0.1)
    raise TimeoutError(f"execution {execution_id} not terminal after {timeout_s}s")


async def _task_status(engine, task_id: int) -> str:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(select(Task.status).where(Task.id == task_id))
        ).scalar_one()


# ════════════════════════════════════════════════════════════════════════════
# E 组 — 全链（矩阵 E）
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rh_e2e_full_chain_to_accepted(rh_env, monkeypatch):
    """E: 202 → QUEUED → RUNNING → Worker → Artifact → EXECUTION_DONE → Gate → ACCEPTED."""
    _mock_llm(monkeypatch)
    project_id, task_id = await _seed_ready_task(rh_env)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # POST /executions → 202 + execution_id（秒回，不等待执行）
        resp = await client.post(
            "/api/executions", json={"project_id": project_id, "task_id": task_id}
        )
        assert resp.status_code == 202  # E: 202 Accepted
        body = resp.json()
        execution_id = body["execution_id"]
        assert body["status"] == "QUEUED"
        assert isinstance(execution_id, int)

        # QUEUED/RUNNING observable via polling (spec §6 first-stage UI)
        status_seen = set()
        for _ in range(100):
            cur = (await client.get(f"/api/executions/{execution_id}")).json()
            status_seen.add(cur["status"])
            if cur["status"] in ("SUCCEEDED", "FAILED"):
                break
            await asyncio.sleep(0.05)
        assert "SUCCEEDED" in status_seen or "QUEUED" in status_seen

    final = await _wait_terminal(rh_env, execution_id)
    assert final == "SUCCEEDED"

    # Task reached EXECUTION_DONE — NOT ACCEPTED by Execution (F red line)
    assert await _task_status(rh_env, task_id) == TaskStatus.EXECUTION_DONE

    # Artifact produced
    async with AsyncSession(rh_env) as s:
        arts = (
            await s.execute(select(Artifact).where(Artifact.task_id == task_id))
        ).scalars().all()
        assert len(arts) == 1

    # Event trail: full chain, ordered, all carry execution_id
    events = await EventEngine().list_for_execution(execution_id)
    types = [e.event_type for e in events]
    assert types[0] == "EXECUTION_QUEUED"
    assert "WORKER_STARTED" in types and "WORKER_COMPLETED" in types
    assert "ARTIFACT_CREATED" in types
    assert types[-1] == "EXECUTION_COMPLETED"
    assert all(e.execution_id == execution_id for e in events)

    # Gate → ACCEPTED (the ONLY acceptance path, actor-guarded)
    from project_runtime.reviewer.gate import GateCore

    async with AsyncSession(rh_env) as s:
        g = await GateCore(s).evaluate(task_id)
        assert g.status.value == "PASS"
        await s.commit()
    assert await _task_status(rh_env, task_id) == TaskStatus.ACCEPTED  # E terminal

    # GATE events recorded with actor=GATE (keyed by task_id — Gate has no
    # execution context, which is correct: Gate is Task-scoped, not Execution-scoped)
    from project_runtime.event.models import RuntimeEvent

    async with AsyncSession(rh_env) as s:
        gate_events = (
            await s.execute(
                select(RuntimeEvent).where(
                    RuntimeEvent.task_id == task_id,
                    RuntimeEvent.event_type.in_(
                        ["GATE_STARTED", "GATE_PASSED", "GATE_FAILED"]
                    ),
                )
            )
        ).scalars().all()
    assert len(gate_events) == 2
    assert all(e.actor == "GATE" for e in gate_events)


# ════════════════════════════════════════════════════════════════════════════
# F 组 — Fail Gate 负断言
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f_execution_survives_fresh_session(rh_env, monkeypatch):
    """F: 页面刷新后 Execution/Event 仍可查（新 session = 新'连接'）。"""
    _mock_llm(monkeypatch)
    project_id, task_id = await _seed_ready_task(rh_env, "f-refresh")

    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    await _wait_terminal(rh_env, ex_id)

    # Fresh session reads (simulates page refresh / new connection)
    async with AsyncSession(rh_env) as s2:
        ex = await s2.get(Execution, ex_id)
        assert ex is not None and ex.status == "SUCCEEDED"
    events = await EventEngine().list_for_execution(ex_id)
    assert len(events) >= 5  # persisted, not in-memory only


@pytest.mark.asyncio
async def test_f_success_never_directly_accepts(rh_env, monkeypatch):
    """F: Execution SUCCEEDED 后 Task 必须停在 EXECUTION_DONE（不越权 ACCEPTED）。"""
    _mock_llm(monkeypatch)
    project_id, task_id = await _seed_ready_task(rh_env, "f-noaccept")

    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    final = await _wait_terminal(rh_env, ex_id)
    assert final == "SUCCEEDED"
    assert await _task_status(rh_env, task_id) == TaskStatus.EXECUTION_DONE
    assert await _task_status(rh_env, task_id) != TaskStatus.ACCEPTED


# ════════════════════════════════════════════════════════════════════════════
# D 组 — 并发场景（矩阵 D）
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d_three_concurrent_full_chain(rh_env, monkeypatch):
    """D: 3 Execution 并发（status/skill/worker/artifact/gate 事件全产生）无持续锁。"""
    _mock_llm(monkeypatch)
    pairs = [await _seed_ready_task(rh_env, f"conc-{i}") for i in range(3)]

    from project_runtime.execution.service import ExecutionService

    service = ExecutionService()
    ex_ids = []
    for pid, tid in pairs:
        ex_ids.append(await service.submit(pid, tid))

    for ex_id in ex_ids:
        assert await _wait_terminal(rh_env, ex_id, timeout_s=30) == "SUCCEEDED"

    # All 3 tasks EXECUTION_DONE; all 3 have artifacts; event order intact
    for pid, tid in pairs:
        assert await _task_status(rh_env, tid) == TaskStatus.EXECUTION_DONE
    async with AsyncSession(rh_env) as s:
        art_count = (
            await s.execute(select(Artifact).where(Artifact.task_id.in_([t for _, t in pairs])))
        ).scalars().all()
        assert len(art_count) == 3
    for ex_id in ex_ids:
        events = await EventEngine().list_for_execution(ex_id)
        ids = [e.id for e in events]
        assert ids == sorted(ids)  # order traceable
        assert events[-1].event_type == "EXECUTION_COMPLETED"


# ════════════════════════════════════════════════════════════════════════════
# 修改单瑕疵 1 抽查 — 事件表无长正文
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_long_messages_in_event_table(rh_env, monkeypatch):
    """修改单-RH03 瑕疵 1 复验: E2E 全链后事件表无长正文。"""
    long_output = "# 标题\n" + ("长输出。" * 500)  # ~1500 chars
    _mock_llm(monkeypatch, output=long_output)

    project_id, task_id = await _seed_ready_task(rh_env, "trunc-check")
    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    await _wait_terminal(rh_env, ex_id)

    events = await EventEngine().list_for_execution(ex_id)
    assert len(events) >= 5
    for e in events:
        assert e.message is None or len(e.message) <= 128
