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
import json

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



async def _wait_task_status(
    engine, task_id: int, expected: str, timeout_s: float = 30.0
) -> str:
    """Poll until Task reaches expected status (Gate runs AFTER the Execution
    terminal event, so terminal-wait alone can race ahead of Phase D)."""
    for _ in range(int(timeout_s / 0.1)):
        if await _task_status(engine, task_id) == expected:
            return expected
        await asyncio.sleep(0.1)
    return await _task_status(engine, task_id)


async def _wait_terminal(engine, execution_id: int, timeout_s: float = 30.0) -> str:
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

    # V1.13+: Gate runs AUTOMATICALLY after SUCCEEDED (Phase D). Phase D
    # happens after the EXECUTION_COMPLETED event, so wait for ACCEPTED
    # explicitly (terminal-wait alone races ahead of the gate).
    got = await _wait_task_status(rh_env, task_id, TaskStatus.ACCEPTED.value)
    assert got == TaskStatus.ACCEPTED.value  # E terminal

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
    """F: 验收只经 GATE 中转——SUCCEEDED 后 ACCEPT 必须来自 Gate（actor=GATE），
    Worker/Execution 永不直接置 ACCEPTED。Phase D 自动 Gate 后语义演进。"""
    _mock_llm(monkeypatch)
    project_id, task_id = await _seed_ready_task(rh_env, "f-noaccept")

    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    final = await _wait_terminal(rh_env, ex_id)
    assert final == "SUCCEEDED"

    # ACCEPTED 必须存在 GATE_PASSED 事件（Gate 是唯一放行通道）
    from project_runtime.event.models import RuntimeEvent

    deadline_wait = 60
    import time as _t
    _start = _t.monotonic()
    while _t.monotonic() - _start < deadline_wait:
        if await _task_status(rh_env, task_id) == TaskStatus.ACCEPTED:
            break
        await asyncio.sleep(0.2)
    assert await _task_status(rh_env, task_id) == TaskStatus.ACCEPTED

    async with AsyncSession(rh_env) as s:
        gate_events = (
            await s.execute(
                select(RuntimeEvent).where(
                    RuntimeEvent.task_id == task_id,
                    RuntimeEvent.event_type == "GATE_PASSED",
                )
            )
        ).scalars().all()
    assert len(gate_events) == 1
    assert gate_events[0].actor == "GATE"  # 放行者只能是 Gate


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

    # All 3 tasks ACCEPTED (auto-gate); all 3 have artifacts; event order intact
    for pid, tid in pairs:
        got = await _wait_task_status(rh_env, tid, TaskStatus.ACCEPTED.value)
        assert got == TaskStatus.ACCEPTED.value
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


@pytest.mark.asyncio
async def test_gate_auto_review_and_downstream_unlock(rh_env, monkeypatch):
    """执行链闭环（04 spec §6）: SUCCEEDED 后自动 Gate → ACCEPTED → HARD 下游解锁.

    此前 /execute 只到 EXECUTION_DONE，Gate 无人触发，HARD 链断在第一个节点
    （用户实测项目 78：524 EXECUTION_DONE 但 525 永远 CREATED）。
    """
    _mock_llm(monkeypatch)
    from project_runtime.project_state.models import TaskDependency

    async with AsyncSession(rh_env) as s:
        p = Project(name="chain", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        mgr = TaskGraphManager(s)
        t1 = await mgr.create_task(Task(project_id=pid, name="first"))
        await mgr.promote_to_ready(t1.id)
        t1_id = t1.id
        t2 = await mgr.create_task(Task(project_id=pid, name="second"))
        t2_id = t2.id  # snapshot BEFORE commit (ORM expiry)
        s.add(TaskDependency(task_id=t2_id, depends_on_id=t1_id, dependency_type="HARD"))
        await s.commit()

    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(pid, t1_id)
    assert await _wait_terminal(rh_env, ex_id) == "SUCCEEDED"

    # Gate ran automatically (Phase D, after terminal event): t1 ACCEPTED
    got = await _wait_task_status(rh_env, t1_id, TaskStatus.ACCEPTED.value)
    assert got == TaskStatus.ACCEPTED.value
    # Downstream HARD dep satisfied → t2 unblocked; FULL_AUTO auto-advance
    # then executes t2 through its own gate → ACCEPTED. Reaching ACCEPTED
    # transitively proves the unblock (t2 could not run otherwise).
    got2 = await _wait_task_status(rh_env, t2_id, TaskStatus.ACCEPTED.value)
    assert got2 == TaskStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_task_name_with_illegal_chars_writes_artifact(rh_env, monkeypatch):
    """Windows 文件名非法字符（如 'Worker / Gate'）不再炸 Worker——名称被清洗。"""
    _mock_llm(monkeypatch)
    async with AsyncSession(rh_env) as s:
        p = Project(name="fs-safe", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(
            Task(project_id=pid, name="17.7 Worker / Gate: test?")
        )
        await mgr.promote_to_ready(t.id)
        tid = t.id
        await s.commit()

    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(pid, tid)
    assert await _wait_terminal(rh_env, ex_id) == "SUCCEEDED"

    # Artifact written with sanitized file name (no / : ?)

    async with AsyncSession(rh_env) as s:
        arts = (
            await s.execute(select(Artifact).where(Artifact.task_id == tid))
        ).scalars().all()
        assert len(arts) == 1
        assert "/" not in arts[0].path and ":" not in arts[0].path.replace(":", "", 1)


@pytest.mark.asyncio
async def test_stranded_running_task_marked_failed(rh_env, monkeypatch):
    """Worker 内部崩溃（artifact 落盘异常）→ 兜底把 RUNNING 任务置 FAILED，不停滞。"""
    # LLM 成功，但 artifact 写盘炸（模拟 591 现场）
    _mock_llm(monkeypatch)

    async def broken_create_artifact(self, **kwargs):
        raise OSError("simulated artifact write failure")

    monkeypatch.setattr(
        "project_runtime.artifact.manager.ArtifactManager.create_artifact",
        broken_create_artifact,
    )

    project_id, task_id = await _seed_ready_task(rh_env, "crash-test")
    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    final = await _wait_terminal(rh_env, ex_id)
    assert final == "FAILED"

    # Task NOT stranded in RUNNING — safety net moved it to FAILED
    got = await _wait_task_status(rh_env, task_id, TaskStatus.FAILED.value)
    assert got == TaskStatus.FAILED.value


@pytest.mark.asyncio
async def test_retry_failed_execution(rh_env, monkeypatch):
    """AE-06 实链: FAILED execution 重试 → 新 attempt, task FAILED→READY→再执行."""
    calls = {"n": 0}

    async def flaky_llm(self, wp):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt fails")
        return "# retry ok"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", flaky_llm
    )

    project_id, task_id = await _seed_ready_task(rh_env, "retry-me")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/executions", json={"project_id": project_id, "task_id": task_id}
        )
        assert r.status_code == 202
        first_id = r.json()["execution_id"]

    assert await _wait_terminal(rh_env, first_id) == "FAILED"
    assert await _task_status(rh_env, task_id) == TaskStatus.FAILED

    # Retry via API — old execution FAILED → task reset READY → new attempt
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/executions/{first_id}/retry")
        assert r.status_code == 202, r.text
        second_id = r.json()["execution_id"]
        assert r.json()["attempt_of"] == first_id

    assert await _wait_terminal(rh_env, second_id) == "SUCCEEDED"
    # attempt_no incremented (AE-06)
    async with AsyncSession(rh_env) as s:
        exs = (
            await s.execute(
                select(Execution).where(Execution.task_id == task_id)
            )
        ).scalars().all()
        attempts = sorted(e.attempt_no for e in exs)
        assert attempts == [1, 2]
    # Gate auto-ran on retry success
    got = await _wait_task_status(rh_env, task_id, TaskStatus.ACCEPTED.value)
    assert got == TaskStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_full_auto_chain_runs_to_completion(rh_env, monkeypatch):
    """FULL_AUTO 语义: 一次触发，整条 HARD 链自动推进到全部 ACCEPTED.

    用户实测痛点: FULL_AUTO 还要每环手点「执行项目」——auto_advance 在
    Gate PASS 后自动提交下游，MANUAL 不受影响。
    """
    _mock_llm(monkeypatch)
    from project_runtime.project_state.models import TaskDependency

    async with AsyncSession(rh_env) as s:
        p = Project(name="auto-chain", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        mgr = TaskGraphManager(s)
        t1 = await mgr.create_task(Task(project_id=pid, name="step-1"))
        await mgr.promote_to_ready(t1.id)
        t1_id = t1.id
        ids = [t1_id]
        prev = t1_id
        for i in (2, 3):
            t = await mgr.create_task(Task(project_id=pid, name=f"step-{i}"))
            tid = t.id
            ids.append(tid)
            s.add(TaskDependency(task_id=tid, depends_on_id=prev, dependency_type="HARD"))
            prev = tid
        await s.commit()

    # ONE submit — the chain must cascade by itself
    from project_runtime.execution.service import ExecutionService

    await ExecutionService().submit(pid, t1_id)

    import time as _t
    deadline = _t.monotonic() + 60
    while _t.monotonic() < deadline:
        statuses = [await _task_status(rh_env, i) for i in ids]
        if all(st == TaskStatus.ACCEPTED.value for st in statuses):
            break
        await asyncio.sleep(0.3)

    for i in ids:
        assert await _task_status(rh_env, i) == TaskStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_bound_skill_injected_into_execution(rh_env, monkeypatch):
    """D17C/D17D 实链: 项目绑定 skill → 执行时 Resolver/Loader 注入 WorkPackage.

    用户实测痛点: skill 绑定了但 execution.skill_id 全 None——Runner 没接
    Resolver/Loader。断言: TaskRun 审计带 skill_id + Worker prompt 含方法指导。
    """
    import io
    import zipfile

    from project_runtime.skills.importer import SkillPackageImporter

    # 1. Import a skill into the test registry
    skill_md = (
        "---\n"
        "skill_id: rh-e2e-skill\n"
        "name: RH E2E Skill\n"
        "version: 1.0.0\n"
        "skill_type: PROJECT_METHOD\n"
        "description: e2e injection test skill\n"
        "---\n\n# 方法\n按四步交付：分析→设计→实现→验证。\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
    import tempfile as _tf
    from pathlib import Path as _Path

    with _tf.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(buf.getvalue())
        zpath = f.name
    # persist to a stable path (loader reads it later)
    stable = _Path("data") / "skills"
    stable.mkdir(parents=True, exist_ok=True)
    zpath2 = stable / "rh_e2e_skill.zip"
    zpath2.write_bytes(buf.getvalue())

    async with AsyncSession(rh_env) as s:
        sk = await SkillPackageImporter(s).import_zip(str(zpath2))
        imported_id = sk.skill_id  # snapshot BEFORE commit (ORM expiry)
        await s.commit()
        assert imported_id == "rh-e2e-skill"

    _mock_llm(monkeypatch)
    project_id, task_id = await _seed_ready_task(rh_env, "with-skill")

    # 2. Bind to the project (TASK_TYPE wildcard — empty scope_key)
    from project_runtime.skills.bindings import SkillBindingManager
    from project_runtime.skills.enums import SkillScopeType

    async with AsyncSession(rh_env) as s:
        await SkillBindingManager(s).bind(
            project_id, SkillScopeType.TASK_TYPE, "", "rh-e2e-skill", "1.0.0"
        )
        await s.commit()

    # 3. Execute — skill must be resolved and injected
    from project_runtime.execution.service import ExecutionService

    ex_id = await ExecutionService().submit(project_id, task_id)
    assert await _wait_terminal(rh_env, ex_id) == "SUCCEEDED"

    # 4. TaskRun audit carries the skill identity
    from project_runtime.project_state.models import TaskRun

    async with AsyncSession(rh_env) as s:
        run = (
            await s.execute(select(TaskRun).where(TaskRun.task_id == task_id))
        ).scalar_one()
        payload = json.loads(run.result)
        assert payload["skill"]["skill_id"] == "rh-e2e-skill"
        assert payload["skill"]["skill_version"] == "1.0.0"

    # 5. Execution row also carries it
    async with AsyncSession(rh_env) as s:
        ex = await s.get(Execution, ex_id)
        assert ex.skill_id == "rh-e2e-skill"

    import os as _os
    _os.unlink(zpath)
