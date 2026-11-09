"""F4: shell 识别已安装 Skill 脚本路径。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.skills.fs_store import install_skill_files
from project_runtime.worker.tools import (
    _resolve_skill_script,
    _skill_args_confined,
)


def test_resolve_installed_script(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    r = install_skill_files({
        "SKILL.md": (
            "---\nname: sh-demo\ndescription: d\n---\n# x\n"
        ).encode("utf-8"),
        "scripts/run.py": b"print(1)\n",
    }, allow_executable=True)
    script = Path(r.install_dir) / "scripts" / "run.py"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    got, skill_root = _resolve_skill_script(
        "python", f'python "{script}"', cwd,
    )
    assert got == script.resolve()
    assert skill_root is not None
    assert _skill_args_confined(
        "out.xlsx --flag", cwd=cwd, skill_root=skill_root,
    )
    assert not _skill_args_confined(
        "C:/Windows/x.xlsx", cwd=cwd, skill_root=skill_root,
    )


def test_non_skill_script_rejected(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    root.mkdir()
    monkeypatch.setattr(
        "project_runtime.skills.fs_store.INSTALLED_ROOT", root,
    )
    cwd = tmp_path / "ws"
    cwd.mkdir()
    evil = cwd / "evil.py"
    evil.write_text("print(1)\n", encoding="utf-8")
    got, _ = _resolve_skill_script("python", f'python "{evil}"', cwd)
    assert got is None


def test_resolve_uses_workspace_venv(tmp_path, monkeypatch):
    import os
    import sys

    from project_runtime.skills.runtime_env import resolve_interpreter_for_script

    script = tmp_path / "s.py"
    script.write_text("print(1)\n", encoding="utf-8")
    ws = tmp_path / "proj"
    if os.name == "nt":
        py = ws / ".venv" / "Scripts" / "python.exe"
    else:
        py = ws / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    exe, prefix = resolve_interpreter_for_script(script, cwd=ws)
    assert Path(exe) == py.resolve() or exe == str(py)
    assert prefix == [str(script)]

    # 无工作区 venv → 系统解释器
    bare = tmp_path / "bare"
    bare.mkdir()
    exe2, _ = resolve_interpreter_for_script(script, cwd=bare)
    assert exe2 == sys.executable
