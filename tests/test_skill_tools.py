"""F3: skills_list / skill_view 工具。"""

from __future__ import annotations

import pytest

from project_runtime.skills.fs_store import install_skill_files
from project_runtime.skills.skills_tools import SkillsListProvider, SkillViewProvider


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    install_skill_files({
        "SKILL.md": (
            "---\nname: view-me\ndescription: For skill_view tests.\n---\n"
            "# Hello Skill\nDo things.\n"
        ).encode("utf-8"),
        "refs/note.md": b"# note\n",
    })
    return root


@pytest.mark.asyncio
async def test_skills_list(installed):
    out = await SkillsListProvider().invoke({})
    assert out["ok"] is True
    assert out["count"] >= 1
    assert any(s["name"] == "view-me" for s in out["skills"])
    assert "view-me" in (out.get("stdout") or "")


@pytest.mark.asyncio
async def test_skill_view_body(installed):
    out = await SkillViewProvider().invoke({"name": "view-me"})
    assert out["ok"] is True
    assert "Hello Skill" in out["content"]
    assert "refs/note.md" in out["files"]
    # 工具循环喂回模型看 stdout（非仅 content）
    assert "Hello Skill" in (out.get("stdout") or "")


@pytest.mark.asyncio
async def test_skill_view_rel_path(installed):
    out = await SkillViewProvider().invoke(
        {"name": "view-me", "path": "refs/note.md"},
    )
    assert out["ok"] is True
    assert "# note" in out["content"]
    assert "# note" in (out.get("stdout") or "")


def test_tool_result_text_uses_content_fallback():
    from project_runtime.worker.llm_worker import _tool_result_text

    assert "BODY" in _tool_result_text({
        "ok": True, "content": "BODY from skill_view",
    })
    assert "VIA_STDOUT" in _tool_result_text({
        "ok": True, "stdout": "VIA_STDOUT", "content": "ignored",
    })


@pytest.mark.asyncio
async def test_skill_view_traversal_denied(installed):
    out = await SkillViewProvider().invoke(
        {"name": "view-me", "path": "../x"},
    )
    assert out["ok"] is False
