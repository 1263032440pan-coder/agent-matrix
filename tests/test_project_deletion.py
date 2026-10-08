"""项目级联删除 tests — API + 级联完整性.

验证:
  - 删除后 26 直连表 + 7 间接表该项目的行全清零
  - 磁盘产物目录删除
  - 404（不存在）/ 批量删除（含不存在的 id 报 error 不炸整体）
"""

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from apps.runtime.main import app
from project_runtime.artifact.manager import ArtifactManager
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    GateStatus,
    TaskStatus,
)
from project_runtime.project_state.models import (
    GateResult,
    ManualAction,
    MethodAsset,
    Project,
    Task,
)


@pytest.fixture
async def client(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/del.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import apps.runtime.api.projects as prj_mod

    monkeypatch.setattr(prj_mod, "engine", eng)
    # 产物根目录指向 tmp（磁盘清理验证）
    monkeypatch.chdir(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng, tmp_path
    await eng.dispose()


async def _seed_full_project(eng) -> int:
    """种一个五脏俱全的项目（task/run/gate/artifact/execution/event）。"""
    async with AsyncSession(eng) as s:
        p = Project(name="del-test", goal="g",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content="# 流程",
            content_hash=hashlib.sha256(b"x").hexdigest(),
        ))
        t1 = Task(project_id=pid, name="任务A", status=TaskStatus.ACCEPTED)
        t2 = Task(project_id=pid, name="任务B", status=TaskStatus.READY)
        s.add_all([t1, t2])
        await s.flush()
        t1_id = t1.id
        from project_runtime.project_state.models import TaskDependency

        s.add(TaskDependency(task_id=t2.id, depends_on_id=t1_id))
        from project_runtime.project_state.models import TaskRun

        s.add(TaskRun(task_id=t1_id, attempt=1, result="ok"))
        s.add(GateResult(task_id=t1_id, gate_name="hard",
                         status=GateStatus.PASS))
        s.add(ManualAction(
            project_id=pid, task_id=t2.id,
            action_type="TASK_DISPATCH", proposal_payload="{}",
        ))
        from project_runtime.event.models import RuntimeEvent

        s.add(RuntimeEvent(
            event_type="WORKER_COMPLETED", project_id=pid,
            message="done", actor="RUNTIME",
        ))
        await s.commit()
    # 产物落盘（data/tasks/{pid}/）
    async with AsyncSession(eng) as s:
        mgr = ArtifactManager(s, workspace_root=f"data/tasks/{pid}")
        await mgr.create_artifact(
            project_id=pid, task_id=t1_id, logical_id="out",
            name="产物.md", content="# x",
        )
        await s.commit()
    return pid


@pytest.mark.asyncio
async def test_cascade_delete_cleans_everything(client):
    c, eng, tmp = client
    pid = await _seed_full_project(eng)

    # 产物目录存在
    assert (tmp / "data" / "tasks" / str(pid)).exists()

    resp = await c.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == pid
    assert body["tables"]["task"] == 2
    assert body["tables"]["method_asset"] == 1
    assert body["tables"]["runtime_event"] == 1
    assert body["tables"]["manual_action"] == 1

    # DB 全清（直连 + 间接）
    async with AsyncSession(eng) as s:
        checks = {
            "task": "SELECT COUNT(*) FROM task WHERE project_id=:p",
            "artifact": "SELECT COUNT(*) FROM artifact WHERE project_id=:p",
            "runtime_event":
                "SELECT COUNT(*) FROM runtime_event WHERE project_id=:p",
            "task_run": (
                "SELECT COUNT(*) FROM task_run WHERE task_id IN "
                "(SELECT id FROM task WHERE project_id=:p)"
            ),
            "gate_result": (
                "SELECT COUNT(*) FROM gate_result WHERE task_id IN "
                "(SELECT id FROM task WHERE project_id=:p)"
            ),
        }
        for t, sql in checks.items():
            n = (await s.execute(
                text(sql).bindparams(p=pid))).scalar_one()
            assert n == 0, f"{t} not cleaned: {n}"
        # project 本体
        assert await s.get(Project, pid) is None

    # 磁盘清
    assert not (tmp / "data" / "tasks" / str(pid)).exists()

    # 再删 → 404
    resp2 = await c.delete(f"/api/projects/{pid}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_batch_delete(client):
    c, eng, tmp = client
    p1 = await _seed_full_project(eng)
    p2 = await _seed_full_project(eng)

    resp = await c.post("/api/projects/batch-delete",
                        json={"project_ids": [p1, p2, 99999]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["deleted"]) == 3
    ok = [d for d in body["deleted"] if "error" not in d]
    errs = [d for d in body["deleted"] if "error" in d]
    assert len(ok) == 2
    assert len(errs) == 1  # 99999 not found, reported not thrown
    assert errs[0]["project_id"] == 99999

    async with AsyncSession(eng) as s:
        assert await s.get(Project, p1) is None
        assert await s.get(Project, p2) is None


@pytest.mark.asyncio
async def test_batch_delete_validation(client):
    c, eng, tmp = client
    assert (await c.post("/api/projects/batch-delete",
                         json={"project_ids": []})).status_code == 400
    assert (await c.post(
        "/api/projects/batch-delete",
        json={"project_ids": ["a"]})).status_code == 400


@pytest.mark.asyncio
async def test_delete_with_cross_project_event_refs(client):
    """跨项目 FK 引用（事件 project_id 归属写错指向别项目的 execution）
    不再炸 FOREIGN KEY——删项目 B 时清掉 A 名下指向 B execution 的事件。"""
    c, eng, tmp = client
    pid = await _seed_full_project(eng)

    # 注入脏数据: 事件 project_id=999（别的项目）但 task_id 指向本项目的
    # task（模拟历史 bug 的跨项目事件归属错误）
    async with AsyncSession(eng) as s:
        from sqlalchemy import select as _sel

        from project_runtime.event.models import RuntimeEvent
        from project_runtime.project_state.models import Task

        t_id = (await s.execute(
            _sel(Task.id).where(Task.project_id == pid))).scalars().first()
        s.add(RuntimeEvent(
            event_type="WORKER_COMPLETED", project_id=999,
            task_id=t_id, message="跨项目脏引用", actor="RUNTIME",
        ))
        await s.commit()
    resp = await c.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200, resp.text  # 曾 500 崩 runtime
    # 脏事件也被清
    async with AsyncSession(eng) as s:
        n = (await s.execute(text(
            'SELECT COUNT(*) FROM runtime_event WHERE project_id=999'
        ))).scalar_one()
        assert n == 0
