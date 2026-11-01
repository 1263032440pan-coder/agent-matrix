"""D17 Dashboard API — read-only project endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import apps.runtime.api.projects as prj_mod
from apps.runtime.main import app
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Project, Task


@pytest.fixture(scope="function")
async def client(tmp_path, monkeypatch):
    # 隔离: tmp 库 + app engine 注入（此前直连开发库——测试污染真数据）
    from sqlalchemy.ext.asyncio import create_async_engine

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
    from project_runtime.project_state.base import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dash.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(prj_mod, "engine", eng)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    c, _ = client
    resp = await c.get("/api/projects")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_dashboard_404(client):
    c, _ = client
    resp = await c.get("/api/projects/99999/dashboard")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_shape(client):
    c, eng = client
    # Seed a project with a task directly via DB (injected test engine)
    async with AsyncSession(eng) as session:
        p = Project(name="dashboard-test")
        session.add(p)
        await session.flush()
        t = Task(project_id=p.id, name="t1", status=TaskStatus.READY)
        session.add(t)
        pid = p.id
        await session.commit()

    resp = await c.get(f"/api/projects/{pid}/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    assert data["project"]["id"] == pid
    assert data["project"]["name"] == "dashboard-test"
    assert any(t["name"] == "t1" for t in data["tasks"])
    assert data["worker_status"]["ready"] >= 1
    assert "artifacts" in data
    assert "gate_results" in data
    assert "gap_count" in data
    assert "readiness" in data
    assert "current_task" in data
    q = data["delivery_quality"]
    assert q["accepted"] == 0
    assert q["soft_accepted"] == 0
    assert q["truncated"] == 0
    assert q["error_codes"] == {}


@pytest.mark.asyncio
async def test_dashboard_delivery_quality_from_events(client):
    """SYS-161: error_code 频次与截断修复次数从工具事件聚合。"""
    import json as _json

    from project_runtime.event.models import RuntimeEvent

    c, eng = client
    async with AsyncSession(eng) as session:
        p = Project(name="quality-test")
        session.add(p)
        await session.flush()
        t = Task(project_id=p.id, name="t1", status=TaskStatus.ACCEPTED)
        session.add(t)
        await session.flush()
        session.add(RuntimeEvent(
            event_type="LLM_TOOL_RESULT",
            project_id=p.id,
            task_id=t.id,
            payload=_json.dumps({"ok": False, "error_code": "TRUNCATED"}),
        ))
        session.add(RuntimeEvent(
            event_type="LLM_TOOL_CALL",
            project_id=p.id,
            task_id=t.id,
            payload=_json.dumps({"tool": "write_file", "repaired": True}),
        ))
        pid = p.id
        await session.commit()

    data = (await c.get(f"/api/projects/{pid}/dashboard")).json()
    q = data["delivery_quality"]
    assert q["accepted"] == 1
    assert q["error_codes"]["TRUNCATED"] == 1
    assert q["truncated"] == 1


# ── WP-018: dashboard 新字段（manual_actions/delivery/frontier）─────


@pytest.mark.asyncio
async def test_dashboard_wp018_fields(client):
    """manual_actions / delivery / frontier 三段可查 + 确认/拒绝端点。"""
    import json as _json

    from sqlalchemy.ext.asyncio import AsyncSession

    from project_runtime.project_state.enums import (
        DeliveryItemStatus,
        ManualActionStatus,
        ManualActionType,
        TaskStatus,
    )
    from project_runtime.project_state.models import (
        DeliveryContract,
        DeliveryItem,
        ManualAction,
        Task,
    )

    c, eng = client
    async with AsyncSession(eng) as s:
        p = Project(name="wp018-test")
        s.add(p)
        await s.flush()
        pid = p.id
        t = Task(project_id=pid, name="任务M", status=TaskStatus.READY)
        s.add(t)
        await s.flush()
        t_id = t.id
        action = ManualAction(
            project_id=pid, task_id=t_id,
            action_type=ManualActionType.TASK_DISPATCH,
            proposal_payload="{}", status=ManualActionStatus.PENDING,
        )
        s.add(action)
        await s.flush()
        action_id = action.id
        contract = DeliveryContract(
            project_id=pid, version=1, items=_json.dumps(["功能A"])
        )
        s.add(contract)
        await s.flush()
        s.add(DeliveryItem(
            contract_id=contract.id, requirement_ref="功能A",
            status=DeliveryItemStatus.PENDING,
        ))
        await s.commit()

    resp = await c.get(f"/api/projects/{pid}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert any(m["id"] == action_id for m in body["manual_actions"])
    assert body["delivery"] and body["delivery"]["items"][0]["requirement_ref"] == "功能A"
    assert t_id in body["frontier"]

    # 拒绝端点
    resp2 = await c.post(f"/api/projects/manual-actions/{action_id}/reject")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "REJECTED"

    # 非 PENDING 再操作 → 409
    resp3 = await c.post(f"/api/projects/manual-actions/{action_id}/confirm")
    assert resp3.status_code == 409


@pytest.mark.asyncio
async def test_dashboard_required_deliverables(client):
    """V1.6c: 任务 playbook_ref + 项目 manifest → required_deliverables。"""
    import json as _json

    c, eng = client
    async with AsyncSession(eng) as session:
        man = {
            "version": 1,
            "deliverables": [
                {
                    "id": "d_ppt",
                    "name": "方案PPT",
                    "match": {"ext": [".pptx"], "min_count": 1},
                },
                {
                    "id": "d_code",
                    "name": "可运行代码",
                    "match": {
                        "ext": [".py"],
                        "any_name_prefix": ["readme"],
                        "min_count": 1,
                    },
                },
            ],
        }
        p = Project(
            name="req-deliv",
            delivery_manifest=_json.dumps(man, ensure_ascii=False),
        )
        session.add(p)
        await session.flush()
        t = Task(
            project_id=p.id,
            name="做PPT",
            status=TaskStatus.READY,
            playbook_ref=_json.dumps({
                "deliverable_ids": ["d_ppt"],
                "stage_outputs": ["方案PPT"],
            }),
        )
        session.add(t)
        t2 = Task(
            project_id=p.id,
            name="理解",
            status=TaskStatus.READY,
            playbook_ref=_json.dumps({"stage_outputs": []}),
        )
        session.add(t2)
        pid = p.id
        await session.commit()

    resp = await c.get(f"/api/projects/{pid}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    tasks = {t["name"]: t for t in body["tasks"]}
    req = tasks["做PPT"]["required_deliverables"]
    assert len(req) == 1
    assert req[0]["id"] == "d_ppt"
    assert req[0]["name"] == "方案PPT"
    assert req[0]["match"]["ext"] == [".pptx"]
    assert tasks["理解"]["required_deliverables"] == []
    cl = body["delivery_checklist"]
    assert cl["total"] == 2
    assert set(i["id"] for i in cl["items"]) == {"d_ppt", "d_code"}
    by_id = {i["id"]: i for i in cl["items"]}
    assert by_id["d_ppt"]["owner_task_name"] == "做PPT"
    assert by_id["d_ppt"]["status"] in (
        "matched", "in_progress", "gap", "pending",
    )
    # matched 数依赖本机 data/tasks/{pid}，不在此硬断言为 0
