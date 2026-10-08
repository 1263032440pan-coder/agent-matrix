"""D17 Dashboard API — read-only project endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.runtime.main import app
from infrastructure.database import engine
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Project, Task


@pytest.fixture(scope="function")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_dashboard_404(client):
    resp = await client.get("/api/projects/99999/dashboard")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_shape(client):
    # Seed a project with a task directly via DB
    async with AsyncSession(engine) as session:
        p = Project(name="dashboard-test")
        session.add(p)
        await session.flush()
        t = Task(project_id=p.id, name="t1", status=TaskStatus.READY)
        session.add(t)
        pid = p.id
        await session.commit()

    resp = await client.get(f"/api/projects/{pid}/dashboard")
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


# ── WP-018: dashboard 新字段（manual_actions/delivery/frontier）─────


@pytest.mark.asyncio
async def test_dashboard_wp018_fields(client):
    """manual_actions / delivery / frontier 三段可查 + 确认/拒绝端点。"""
    import json as _json

    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.database import engine
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

    async with AsyncSession(engine) as s:
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

    resp = await client.get(f"/api/projects/{pid}/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert any(m["id"] == action_id for m in body["manual_actions"])
    assert body["delivery"] and body["delivery"]["items"][0]["requirement_ref"] == "功能A"
    assert t_id in body["frontier"]

    # 拒绝端点
    resp2 = await client.post(f"/api/projects/manual-actions/{action_id}/reject")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "REJECTED"

    # 非 PENDING 再操作 → 409
    resp3 = await client.post(f"/api/projects/manual-actions/{action_id}/confirm")
    assert resp3.status_code == 409
