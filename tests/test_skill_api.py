"""Skill package HTTP API tests — import/list/bind/enable via real ASGI layer."""

import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import apps.runtime.api.skills as api_skills
import project_runtime.project_state.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
from apps.runtime.main import app
from project_runtime.project_state.base import Base

SKILL_MD = """---
skill_id: ui-test-skill
name: UI Test Skill
version: 1.0.0
skill_type: TASK_EXECUTION
description: A test skill package for HTTP API tests.
---

# UI Test Skill

How to do the thing, step by step.
"""

GUIDE = "# Guide\n\nDetailed reference material.\n"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", SKILL_MD)
        zf.writestr("references/guide.md", GUIDE)
    return buf.getvalue()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/skills.db")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(api_skills, "engine", test_engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_import_list_bind_enable_roundtrip(client):
    # 1. Import the ZIP package
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("ui-test-skill.zip", _zip_bytes(), "application/zip")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["skill_id"] == "ui-test-skill"
    assert body["version"] == "1.0.0"
    assert body["status"] == "INSTALLED"

    # 2. List shows it
    resp = await client.get("/api/skills")
    assert resp.status_code == 200
    ids = [s["skill_id"] for s in resp.json()]
    assert "ui-test-skill" in ids

    # 3. Bind to a project (needs a project row: create directly in DB)
    engine = api_skills.engine
    from project_runtime.project_state.enums import AutomationMode
    from project_runtime.project_state.models import Project

    async with AsyncSession(engine) as s:
        p = Project(name="skill-bind-test", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id  # snapshot BEFORE commit (ORM expiry)
        await s.commit()

    resp = await client.post(
        f"/api/skills/bindings/project/{pid}",
        json={"skill_id": "ui-test-skill", "skill_version": "1.0.0", "scope_key": "需求"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["skill_id"] == "ui-test-skill"

    # 4. Bindings listing
    resp = await client.get(f"/api/skills/bindings/project/{pid}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 5. Disable + enable
    resp = await client.post("/api/skills/ui-test-skill/1.0.0/disable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "DISABLED"
    resp = await client.post("/api/skills/ui-test-skill/1.0.0/enable")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_import_rejects_executable(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", SKILL_MD)
        zf.writestr("evil.py", "print('pwned')")
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("bad.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 422
    assert "Executable" in resp.json()["detail"] or "rejected" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_rejects_non_zip(client):
    resp = await client.post(
        "/api/skills/import",
        files={"file": ("notes.md", b"# not a zip", "text/markdown")},
    )
    assert resp.status_code == 400
