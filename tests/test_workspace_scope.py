"""自定义工作区路径禁闭统一（读=项目根 / 写=任务根）。"""

from __future__ import annotations

import pytest

from project_runtime.workspace import (
    derive_project_workspace,
    read_allow_root,
    write_allow_root,
)


def test_write_allow_root_is_task_ws(tmp_path):
    task = tmp_path / ".agent-matrix" / "tasks" / "9"
    assert write_allow_root(task) == task.resolve()


def test_read_allow_root_prefers_explicit_project(tmp_path):
    project = (tmp_path / "repo").resolve()
    task = project / ".agent-matrix" / "tasks" / "3"
    assert read_allow_root(project_ws=project, task_ws=task) == project


def test_derive_custom_agent_matrix_layout(tmp_path):
    project = (tmp_path / "repo").resolve()
    task = project / ".agent-matrix" / "tasks" / "3"
    task.mkdir(parents=True)
    assert derive_project_workspace(task) == project
    assert read_allow_root(project_ws=None, task_ws=task) == project


def test_derive_default_data_tasks_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = (tmp_path / "data" / "tasks" / "2" / "15").resolve()
    task.mkdir(parents=True)
    # 默认布局：读根放宽到 data/tasks，兼容跨任务只读
    assert read_allow_root(project_ws=None, task_ws=task) == (
        tmp_path / "data" / "tasks"
    ).resolve()


@pytest.mark.asyncio
async def test_office_inspect_ok_under_custom_workspace(tmp_path):
    from project_runtime.office.providers import (
        ImageRasterProvider,
        OfficeInspectProvider,
    )

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "223"
    task.mkdir(parents=True)
    payload_ws = {
        "__ws_root": str(task),
        "__project_ws_root": str(project),
    }
    img = await ImageRasterProvider().invoke({
        **payload_ws,
        "path": "deliverables/prototype/prototype.png",
        "spec": {"title": "原型", "lines": ["A", "B"]},
    })
    assert img["ok"], img
    insp = await OfficeInspectProvider().invoke({
        **payload_ws,
        "path": "deliverables/prototype/prototype.png",
    })
    assert insp["ok"], insp
    assert insp.get("kind") == "image"


@pytest.mark.asyncio
async def test_office_inspect_out_of_project_scope(tmp_path):
    from project_runtime.office.providers import OfficeInspectProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "1"
    task.mkdir(parents=True)
    outside = tmp_path / "other" / "x.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    r = await OfficeInspectProvider().invoke({
        "__ws_root": str(task),
        "__project_ws_root": str(project),
        "path": str(outside),
    })
    assert r["ok"] is False
    assert r["error_code"] == "OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_read_file_ok_under_custom_workspace(tmp_path):
    from project_runtime.worker.tools import ReadFileProvider, WriteFileProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "5"
    task.mkdir(parents=True)
    ws = {"__ws_root": str(task), "__project_ws_root": str(project)}
    w = await WriteFileProvider().invoke({
        **ws, "path": "note.md", "content": "HELLO",
    })
    assert w["ok"], w
    r = await ReadFileProvider().invoke({**ws, "path": "note.md"})
    assert r["ok"] and r["stdout"] == "HELLO"


@pytest.mark.asyncio
async def test_shell_cwd_locks_task_ws(tmp_path):
    from project_runtime.worker.tools import ShellProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "7"
    (task / "deliverables").mkdir(parents=True)
    (task / "deliverables" / "marker.txt").write_text("x", encoding="utf-8")

    r = await ShellProvider().invoke({
        "__ws_root": str(task),
        "__project_ws_root": str(project),
        "command": "dir deliverables" if __import__("os").name == "nt" else "ls deliverables",
    })
    assert r["ok"], r
    assert "marker.txt" in (r.get("stdout") or "")


@pytest.mark.asyncio
async def test_todo_under_custom_workspace(tmp_path):
    from project_runtime.worker.coding_tools import TodoProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "8"
    task.mkdir(parents=True)
    p = TodoProvider()
    r = await p.invoke({
        "__ws_root": str(task),
        "__project_ws_root": str(project),
        "action": "add",
        "text": "收尾禁闭",
    })
    assert r["ok"], r
    assert (task / ".todo.json").exists()


@pytest.mark.asyncio
async def test_code_search_under_custom_workspace(tmp_path):
    from project_runtime.worker.tools import CodeSearchProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "9"
    task.mkdir(parents=True)
    (task / "hit.py").write_text("VALUE_MARKER = 1\n", encoding="utf-8")
    r = await CodeSearchProvider().invoke({
        "__ws_root": str(task),
        "__project_ws_root": str(project),
        "pattern": "VALUE_MARKER",
        "glob": "*.py",
        "scope": ".",
    })
    assert r["ok"], r
    assert r["count"] >= 1


@pytest.mark.asyncio
async def test_code_search_outside_project_rejected(tmp_path):
    from project_runtime.worker.tools import CodeSearchProvider

    project = tmp_path / "user-repo"
    task = project / ".agent-matrix" / "tasks" / "9"
    task.mkdir(parents=True)
    outside = tmp_path / "other"
    outside.mkdir()
    r = await CodeSearchProvider().invoke({
        "__ws_root": str(task),
        "__project_ws_root": str(project),
        "pattern": "x",
        "scope": str(outside),
    })
    assert r["ok"] is False
    assert r.get("error_code") == "OUT_OF_SCOPE" or "越界" in str(r.get("error"))
