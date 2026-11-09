"""F1: 文件系统 Skill 安装（agentskills.io 对齐）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from project_runtime.skills.fs_store import (
    install_from_directory,
    install_from_zip,
    install_skill_files,
    list_installed_dirs,
    uninstall_skill,
)
from project_runtime.skills.importer import SkillImportError


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    return root


def _text_skill_md(
    name: str = "demo-text",
    *,
    full: bool = False,
) -> str:
    if full:
        return (
            "---\n"
            f"skill_id: {name}\n"
            f"name: {name}\n"
            "version: 1.0.0\n"
            "skill_type: PROJECT_METHOD\n"
            "description: text only skill\n"
            "---\n# Body\n"
        )
    # Claude / agentskills 最小字段
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use when demo text guidance is needed.\n"
        "---\n# Body\nHello\n"
    )


def test_install_minimal_frontmatter(installed):
    files = {"SKILL.md": _text_skill_md("hello-world").encode("utf-8")}
    r = install_skill_files(files, allow_executable=False)
    assert r.skill_id == "hello-world"
    assert "skill_id" in r.adapted_fields
    assert "version" in r.adapted_fields
    assert "skill_type" in r.adapted_fields
    assert (Path(r.install_dir) / "SKILL.md").is_file()
    assert list_installed_dirs()


def test_install_rejects_py_without_allow(installed):
    files = {
        "SKILL.md": _text_skill_md("with-script").encode("utf-8"),
        "scripts/run.py": b"print(1)\n",
    }
    with pytest.raises(SkillImportError, match="Executable|允许执行型"):
        install_skill_files(files, allow_executable=False)


def test_install_allows_py_with_allow(installed):
    files = {
        "SKILL.md": _text_skill_md("with-script").encode("utf-8"),
        "scripts/run.py": b"print(1)\n",
    }
    r = install_skill_files(files, allow_executable=True)
    assert r.has_scripts is True
    assert (Path(r.install_dir) / "scripts" / "run.py").is_file()


def test_install_multi_skill_zip(installed, tmp_path):
    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(
            "bundle/skills/alpha/SKILL.md",
            _text_skill_md("alpha"),
        )
        zf.writestr(
            "bundle/skills/beta/SKILL.md",
            _text_skill_md("beta"),
        )
        zf.writestr("bundle/README.md", "# hi\n")
    results = install_from_zip(zpath, allow_executable=False)
    names = sorted(r.skill_id for r in results)
    assert names == ["alpha", "beta"]
    assert (installed / "alpha" / "SKILL.md").is_file()
    assert (installed / "beta" / "SKILL.md").is_file()


def test_install_from_directory_single(installed, tmp_path):
    d = tmp_path / "one"
    d.mkdir()
    (d / "SKILL.md").write_text(_text_skill_md("dir-skill"), encoding="utf-8")
    results = install_from_directory(d)
    assert len(results) == 1
    assert results[0].skill_id == "dir-skill"


def test_uninstall(installed):
    files = {"SKILL.md": _text_skill_md("todel").encode("utf-8")}
    install_skill_files(files)
    assert uninstall_skill("todel") is True
    assert not (installed / "todel").exists()


def test_full_frontmatter_no_adapt_noise(installed):
    files = {"SKILL.md": _text_skill_md("full-one", full=True).encode("utf-8")}
    r = install_skill_files(files)
    assert r.version == "1.0.0"
    assert r.skill_type == "PROJECT_METHOD"
    # when_to_use 仍可能被补
    assert r.skill_id == "full-one"
