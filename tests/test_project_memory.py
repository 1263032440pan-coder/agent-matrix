"""WP-020 Project Memory Manager tests — acceptance checklist.

Acceptance:
  [x] 五类 Memory 有统一入口
  [x] Retrieval 只依赖 RetrievalProvider（WP-025 contract）
  [x] Worker Session 退出不影响 Memory
  [x] Event / Artifact 可作为 Memory Source
"""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory.manager as mem_mgr_mod
import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.memory import ProjectMemoryManager
from project_runtime.project_state.base import Base
from project_runtime.retrieval import (
    FTS5Indexer,
    FTS5RetrievalProvider,
    RetrievalDocumentRepository,
    ensure_fts_table,
)


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mem.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        await ensure_fts_table(s)
    yield eng
    await eng.dispose()


async def _seed_project(dbengine, pid: int = 1):
    from project_runtime.project_state.enums import AutomationMode, TaskStatus
    from project_runtime.project_state.models import Artifact, Project, Task

    async with AsyncSession(dbengine) as s:
        p = Project(name="mem-test", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        assert p.id == pid
        t = Task(project_id=pid, name="task-1", status=TaskStatus.ACCEPTED)
        s.add(t)
        await s.flush()
        s.add(Artifact(
            logical_artifact_id="a-1", project_id=pid, task_id=t.id,
            name="out.md", version=1, path="data/x.md", sha256="h1",
        ))
        s.add(Artifact(
            logical_artifact_id="a-1", project_id=pid, task_id=t.id,
            name="out.md", version=2, path="data/x2.md", sha256="h2",
        ))
        tid = t.id  # snapshot BEFORE commit (ORM expiry)
        await s.commit()
        return tid


# ── §1 五类 Memory 统一入口 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_unified_entry_all_five(dbengine):
    tid = await _seed_project(dbengine)
    mgr = ProjectMemoryManager(engine=dbengine)

    state = await mgr.get_state_memory(1)
    assert state["project"]["name"] == "mem-test"
    assert state["tasks"][0]["status"] == "ACCEPTED"

    arts = await mgr.get_artifact_memory(1)
    # latest version per logical id
    assert len(arts) == 1 and arts[0]["version"] == 2

    summaries = await mgr.get_summary_memory(1)  # WP-021 未落地 → 空但可用
    assert summaries == []

    execution = await mgr.get_execution_memory(1)
    assert "events" in execution and "task_runs" in execution

    version = await mgr.get_memory_version(1)
    assert version["project_id"] == 1 and "as_of" in version


# ── §2 Retrieval 只依赖 Provider ──────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_only_via_providers(dbengine):
    async with AsyncSession(dbengine) as s:
        repo = RetrievalDocumentRepository(s)
        idx = FTS5Indexer(s)
        d = await repo.upsert(
            project_id=1, source="ARTIFACT", source_id="d1",
            title="d1", content="架构设计文档",
        )
        if d:
            await idx.index(d.id, "d1", "架构设计文档")
        await s.commit()

    # Manager has NO direct retriever — only the registry contract
    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))
        mgr = ProjectMemoryManager(registry=registry)
        r = await mgr.retrieve("架构", project_id=1)
        assert r["providers"] == ["fts5-retrieval"]
        assert r["total"] == 1

    # Without a registry the manager degrades — it NEVER imports a
    # concrete retriever itself
    import inspect

    src = inspect.getsource(mem_mgr_mod)
    assert "FTS5Retriever(" not in src  # only the provider via orchestrator


# ── §3 Worker Session 退出不影响 Memory ────────────────────────────────


@pytest.mark.asyncio
async def test_memory_survives_session_exit(dbengine):
    await _seed_project(dbengine)
    mgr = ProjectMemoryManager(engine=dbengine)

    # A worker-style session is created and CLOSED (simulating exit)...
    async with AsyncSession(dbengine) as worker_session:
        registry = ExtensionRegistry()
        registry.register(FTS5RetrievalProvider(worker_session))
        mgr_with_worker = ProjectMemoryManager(registry=registry, engine=dbengine)
        r = await mgr_with_worker.retrieve("任何", project_id=1)
        assert isinstance(r, dict)
    # ...worker session now closed.

    # Manager memory access is unaffected — it opens its OWN sessions
    state = await mgr.get_state_memory(1)
    assert state["project"]["name"] == "mem-test"
    arts = await mgr.get_artifact_memory(1)
    assert len(arts) == 1
    version = await mgr.get_memory_version(1)
    assert version["project_id"] == 1


# ── §4 Event / Artifact 作为 Memory Source ────────────────────────────


@pytest.mark.asyncio
async def test_events_and_artifacts_as_sources(dbengine):
    import project_runtime.event.engine as evt_mod
    from project_runtime.event import EventEngine, RuntimeEventType

    await _seed_project(dbengine)

    # Patch event engine to the test DB, record a real event
    orig = evt_mod.engine
    evt_mod.engine = dbengine
    try:
        await EventEngine().record(
            RuntimeEventType.EXECUTION_QUEUED,
            project_id=1, task_id=1, execution_id=99,
            phase="QUEUED", message="memory source test",
        )
    finally:
        evt_mod.engine = orig

    mgr = ProjectMemoryManager(engine=dbengine)
    execution = await mgr.get_execution_memory(1)
    # Event readable as execution memory
    assert any(e["message"] == "memory source test" for e in execution["events"])

    # Artifact readable as artifact memory (both are memory SOURCES)
    arts = await mgr.get_artifact_memory(1)
    assert arts[0]["logical_id"] == "a-1"

    # memory version advances with the event
    version = await mgr.get_memory_version(1)
    assert version["execution_version"] >= 1
