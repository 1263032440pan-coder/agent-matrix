"""项目 / 任务工作区解析。"""


import pytest

from project_runtime.workspace import (
    resolve_project_workspace,
    resolve_task_workspace,
    validate_workspace_path,
)


def test_default_project_and_task_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = resolve_project_workspace(7, None)
    assert p == (tmp_path / "data" / "tasks" / "7").resolve()
    t = resolve_task_workspace(7, 3, None)
    assert t == (tmp_path / "data" / "tasks" / "7" / "3").resolve()


def test_custom_workspace_uses_agent_matrix_tasks(tmp_path):
    root = tmp_path / "user-repo"
    root.mkdir()
    p = resolve_project_workspace(7, str(root))
    assert p == root.resolve()
    t = resolve_task_workspace(7, 3, str(root))
    assert t == (root / ".agent-matrix" / "tasks" / "3").resolve()


def test_validate_requires_absolute(tmp_path):
    with pytest.raises(ValueError, match="绝对路径"):
        validate_workspace_path("relative/path")
    abs_p = tmp_path / "ws"
    got = validate_workspace_path(str(abs_p), must_exist=False)
    assert got == abs_p.resolve()
