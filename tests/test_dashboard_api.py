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
