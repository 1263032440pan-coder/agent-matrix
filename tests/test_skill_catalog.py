"""F2: Skill 目录扫描与 XML 注入。"""

from __future__ import annotations

from project_runtime.skills.catalog import (
    format_available_skills_xml,
    list_catalog,
)
from project_runtime.skills.fs_store import install_skill_files


def test_list_catalog_installed(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    install_skill_files({
        "SKILL.md": (
            "---\nname: cat-a\ndescription: Catalog skill A for tests.\n---\n# A\n"
        ).encode("utf-8"),
    })
    entries = list_catalog(include_user_agents=False)
    assert any(e.name == "cat-a" for e in entries)
    xml = format_available_skills_xml(entries)
    assert "<available_skills>" in xml
    assert 'name="cat-a"' in xml
    assert "Catalog skill A" in xml


def test_preferred_sorting(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    for n in ("zzz", "aaa"):
        install_skill_files({
            "SKILL.md": (
                f"---\nname: {n}\ndescription: desc {n}\n---\n# x\n"
            ).encode("utf-8"),
        })
    entries = list_catalog(
        include_user_agents=False, preferred_names={"zzz"},
    )
    assert entries[0].name == "zzz"
    assert entries[0].preferred is True


def test_s12_db_without_fs_not_in_catalog(tmp_path, monkeypatch):
    """S12：仅有 DB 镜像、无 installed/ 目录 → 不进目录；API 标缺失。"""
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    entries = list_catalog(include_user_agents=False)
    assert all(e.name != "orphan-db-only" for e in entries)

    class _Sk:
        skill_id = "orphan-db-only"
        name = "orphan-db-only"
        package_ref = None

    from apps.runtime.api.skills import _fs_dir_missing, _serialize

    assert _fs_dir_missing(_Sk()) is True
    ser = _serialize(
        type(
            "SK",
            (),
            {
                "skill_id": "orphan-db-only",
                "name": "orphan-db-only",
                "version": "0.0.1",
                "skill_type": None,
                "description": "x",
                "when_to_use": None,
                "domain": None,
                "task_types": None,
                "status": None,
                "content_hash": "abc",
                "package_ref": None,
                "created_at": None,
            },
        )()
    )
    assert ser["missing_file"] is True


def test_decompose_prompt_includes_catalog(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    install_skill_files({
        "SKILL.md": (
            "---\nname: plan-cat\ndescription: For planning inject.\n---\n# P\n"
        ).encode("utf-8"),
    })
    from project_runtime.procedure.plan_decompose import build_decompose_prompt

    prompt = build_decompose_prompt(
        T={
            "task_key": "t1",
            "role": "implement",
            "deliverable_ids": [],
            "load": 9,
            "obligation_tokens": [{"id": "a"}],
            "contract": "c",
        },
        G={"nodes": {}, "replaced": {}},
        remaining_queue=[],
    )
    assert "<available_skills>" in prompt
    assert "plan-cat" in prompt
