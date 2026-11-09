"""SYS-147/149/150: read_file / list_dir / write_file（拆分自 file_rw）。"""

import pytest

from project_runtime.worker.tools import (
    ListDirProvider,
    ReadFileProvider,
    WriteFileProvider,
    expand_tool_whitelist,
)

WS = "data/tasks/2/15"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / WS).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/tasks/2/14").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/tasks/2/14/up.md").write_text("UP", encoding="utf-8")
    return tmp_path


def _ws(payload: dict) -> dict:
    return {**payload, "__ws_root": WS}


# ── write_file ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_creates_and_confirms(workspace):
    r = await WriteFileProvider().invoke(_ws({
        "path": "src/main/java/App.java", "content": "class App {}",
    }))
    assert r["ok"] is True
    assert "src/main/java/App.java" in r["stdout"]
    assert "现有 1 个文件" in r["stdout"]
    assert (workspace / WS / "src/main/java/App.java").read_text(
        encoding="utf-8") == "class App {}"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, missing", [
    ({"path": "README.md"}, "content"),
    ({"content": "x"}, "path"),
    ({"path": "README.md", "content": "   "}, "content"),
])
async def test_write_missing_field(workspace, payload, missing):
    r = await WriteFileProvider().invoke(_ws(payload))
    assert r["ok"] is False
    assert r["error_code"] == "MISSING_FIELD"
    assert missing in r["error"]
    assert "write_file" in r["hint"]
    assert not any((workspace / WS).iterdir())


@pytest.mark.asyncio
async def test_write_other_task_out_of_scope(workspace):
    r = await WriteFileProvider().invoke(_ws({
        "path": "data/tasks/2/20/test.txt", "content": "x",
    }))
    assert r["ok"] is False
    assert r["error_code"] == "OUT_OF_SCOPE"
    assert "只读" in r["hint"]
    assert not (workspace / "data/tasks/2/20").exists()


@pytest.mark.asyncio
async def test_write_traversal_blocked(workspace):
    r = await WriteFileProvider().invoke(_ws({
        "path": "../../../escape.md", "content": "x",
    }))
    assert r["ok"] is False and r["error_code"] == "OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_write_repaired_flag_warns(workspace):
    r = await WriteFileProvider().invoke(_ws({
        "path": "README.md", "content": "# t", "__repaired": True,
    }))
    assert r["ok"] is True and "可能不完整" in r["stdout"]


@pytest.mark.asyncio
async def test_write_validator_script_forbidden(workspace):
    from project_runtime.procedure.delivery_validator import SCRIPT_NAME

    r = await WriteFileProvider().invoke(_ws({
        "path": SCRIPT_NAME, "content": "x",
    }))
    assert r["ok"] is False and r["error_code"] == "FORBIDDEN_PATH"


@pytest.mark.asyncio
async def test_write_no_ws_root_legacy(workspace):
    r = await WriteFileProvider().invoke({
        "path": "data/tasks/anywhere/x.py", "content": "x",
    })
    assert r["ok"] is True


# ── 读/列/写同一相对路径语义（SYS-149）────────────────────────────


@pytest.mark.asyncio
async def test_write_then_read_and_list_same_file(workspace):
    await WriteFileProvider().invoke(_ws({
        "path": "README.md", "content": "HELLO",
    }))
    r = await ReadFileProvider().invoke(_ws({"path": "README.md"}))
    assert r["ok"] is True and r["stdout"] == "HELLO"
    ls = await ListDirProvider().invoke(_ws({}))
    assert ls["ok"] is True and "README.md" in ls["items"]


@pytest.mark.asyncio
async def test_task_prefix_forms_resolve_to_task(workspace):
    for p in ("data/tasks/2/15/a.md", "2/15/b.md", "./c.md"):
        r = await WriteFileProvider().invoke(_ws({"path": p, "content": "x"}))
        assert r["ok"] is True, p
    names = sorted(f.name for f in (workspace / WS).iterdir())
    assert names == ["a.md", "b.md", "c.md"]


@pytest.mark.asyncio
async def test_read_upstream_task_allowed(workspace):
    r = await ReadFileProvider().invoke(_ws({"path": "data/tasks/2/14/up.md"}))
    assert r["ok"] is True and r["stdout"] == "UP"


@pytest.mark.asyncio
async def test_list_subdir_items_are_ws_relative(workspace):
    await WriteFileProvider().invoke(_ws({
        "path": "src/main/App.java", "content": "x",
    }))
    ls = await ListDirProvider().invoke(_ws({"path": "src/main"}))
    assert ls["items"] == ["src/main/App.java"]


# ── read_file / list_dir 失败 ────────────────────────────────────


@pytest.mark.asyncio
async def test_read_missing_file_not_found(workspace):
    r = await ReadFileProvider().invoke(_ws({"path": "nope.md"}))
    assert r["ok"] is False and r["error_code"] == "NOT_FOUND"
    assert "write_file" in r["hint"]


@pytest.mark.asyncio
async def test_read_missing_path(workspace):
    r = await ReadFileProvider().invoke(_ws({}))
    assert r["ok"] is False and r["error_code"] == "MISSING_FIELD"


@pytest.mark.asyncio
async def test_read_outside_data_tasks(workspace):
    r = await ReadFileProvider().invoke({"path": "C:/Windows/win.ini"})
    assert r["ok"] is False and r["error_code"] == "OUT_OF_SCOPE"


# ── 白名单别名展开 ───────────────────────────────────────────────


def test_expand_whitelist_alias():
    assert expand_tool_whitelist(["file_rw", "web_search"]) == [
        "read_file", "list_dir", "write_file", "append_file", "web_search",
        "skills_list", "skill_view",
    ]
    assert expand_tool_whitelist(["tool:file_rw", "read_file"]) == [
        "read_file", "list_dir", "write_file", "append_file",
        "skills_list", "skill_view",
    ]
    assert expand_tool_whitelist(None) is None


def test_schemas_have_no_action_and_required():
    from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

    assert "tool:file_rw" not in TOOL_SCHEMAS
    for name in ("tool:read_file", "tool:list_dir", "tool:write_file"):
        props = TOOL_SCHEMAS[name]["parameters"]["properties"]
        assert "action" not in props, name
    assert TOOL_SCHEMAS["tool:write_file"]["parameters"]["required"] == [
        "path", "content",
    ]
