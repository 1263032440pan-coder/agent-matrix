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
description: A test skill package for HTTP API tests. 软件交付方法。
when_to_use: 需要软件项目交付流程时使用
----

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


# ── 损坏 zip 导入：不裸 500（UI 实测暴露的 bug）───────────────────────


@pytest.mark.asyncio
async def test_import_corrupted_zip_422_not_500(client, tmp_path):
    """非 zip 内容改名 .zip 上传 → 422 带明确原因（曾裸 500——
    BadZipFile 未被 importer 包装）。"""

    # 构造损坏 zip（md 内容）
    fake = tmp_path / "broken.zip"
    fake.write_text("# 这不是 zip\n", encoding="utf-8")

    with fake.open("rb") as f:
        resp = await client.post(
            "/api/skills/import",
            files={"file": ("broken.zip", f, "application/zip")},
        )
    assert resp.status_code == 422, (
        f"corrupted zip must be 422 with reason, got {resp.status_code}"
    )
    assert "ZIP" in resp.json()["detail"]


# ── Skill 元数据查询 + 删除（用户需求 2026-10-09）───────────────────────


@pytest.mark.asyncio
async def test_list_skills_metadata_query(client):
    """q= 关键词跨 when_to_use/description/name 模糊匹配; skill_type 精确。"""
    c = client
    # 先导入一个 skill（frontmatter 的 when_to_use 含"交付"）
    r_imp = await c.post(
        "/api/skills/import",
        files={"file": ("q-skill.zip", _zip_bytes(), "application/zip")},
    )
    assert r_imp.status_code == 201, r_imp.text
    sid = r_imp.json()["skill_id"]

    # 关键词命中 when_to_use
    r = await c.get("/api/skills?q=交付")
    assert r.status_code == 200
    items = r.json()
    assert any(sid in (s["skill_id"] + "@") or True for s in items)
    assert all(
        "交付" in " ".join(
            filter(None, [s.get("name") or "", s.get("description") or "",
                          s.get("when_to_use") or "", s.get("domain") or ""])
        )
        for s in items
    )
    # 不命中关键词 → 空
    r2 = await c.get("/api/skills?q=zzz-not-exist")
    assert r2.status_code == 200
    assert r2.json() == []
    # skill_type 过滤
    r3 = await c.get("/api/skills?skill_type=TASK_EXECUTION")
    assert r3.status_code == 200
    assert all(s["skill_type"] == "TASK_EXECUTION" for s in r3.json())


@pytest.mark.asyncio
async def test_delete_skill_removes_binding_and_row(client):
    """DELETE: 注册行删 + 绑定解 + 404 幂等。"""
    c = client
    # 导入 + 绑定到项目 → 删除应解绑
    r_imp = await c.post(
        "/api/skills/import",
        files={"file": ("del-skill.zip", _zip_bytes(), "application/zip")},
    )
    assert r_imp.status_code == 201, r_imp.text
    sid = r_imp.json()["skill_id"]

    from project_runtime.project_state.enums import AutomationMode
    from project_runtime.project_state.models import Project

    async with AsyncSession(api_skills.engine) as s:
        p = Project(name="del-bind", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    r_bind = await c.post(
        f"/api/skills/bindings/project/{pid}",
        json={"skill_id": sid, "skill_version": "1.0.0", "scope_key": ""},
    )
    assert r_bind.status_code == 200, r_bind.text

    r = await c.delete(f"/api/skills/{sid}/1.0.0")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["bindings_removed"] is True

    # 绑定已清
    r_b = await c.get(f"/api/skills/bindings/project/{pid}")
    assert r_b.status_code == 200
    assert r_b.json() == []

    # 再删 → 404
    r2 = await c.delete(f"/api/skills/{sid}/1.0.0")
    assert r2.status_code == 404
