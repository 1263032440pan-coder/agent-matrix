"""问题1测试: file_rw write 实现 + 三 action stdout 可见性。"""

import pytest

from project_runtime.worker.tools import FileRwProvider


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data/tasks/w").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.mark.asyncio
async def test_write_creates_and_confirms(workspace):
    """write 落盘 + 返回确认文本（模型可见自己写了什么）。"""
    p = FileRwProvider()
    r = await p.invoke({
        "action": "write", "path": "data/tasks/w/doc.md",
        "content": "# 标题\n正文",
    })
    assert r["ok"] is True
    assert "已写入" in r["stdout"] and "doc.md" in r["stdout"]
    assert (workspace / "data/tasks/w/doc.md").read_text(
        encoding="utf-8") == "# 标题\n正文"


@pytest.mark.asyncio
async def test_write_requires_content(workspace):
    p = FileRwProvider()
    r = await p.invoke({"action": "write", "path": "data/tasks/w/x.md"})
    assert r["ok"] is False
    assert "content" in r["error"]


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(workspace):
    """嵌套路径自动建目录（模型编的深层路径不再失败）。"""
    p = FileRwProvider()
    r = await p.invoke({
        "action": "write", "path": "data/tasks/w/a/b/c.md", "content": "x",
    })
    assert r["ok"] is True
    assert (workspace / "data/tasks/w/a/b/c.md").exists()


@pytest.mark.asyncio
async def test_read_returns_stdout(workspace):
    """read 的内容通过 stdout 键透出（分发层取的字段——模型可见）。"""
    p = FileRwProvider()
    await p.invoke({
        "action": "write", "path": "data/tasks/w/r.md", "content": "READABLE",
    })
    r = await p.invoke({"action": "read", "path": "data/tasks/w/r.md"})
    assert r["ok"] is True
    assert r["stdout"] == "READABLE"
    assert r["content"] == "READABLE"


@pytest.mark.asyncio
async def test_list_returns_stdout(workspace):
    """list 的条目通过 stdout 透出。"""
    p = FileRwProvider()
    await p.invoke({
        "action": "write", "path": "data/tasks/w/one.md", "content": "1",
    })
    r = await p.invoke({"action": "list", "path": "data/tasks/w"})
    assert r["ok"] is True
    assert "one.md" in r["stdout"]


@pytest.mark.asyncio
async def test_write_traversal_blocked(workspace):
    """写路径仍受工作区禁闭（防穿越不因新分支放松）。"""
    p = FileRwProvider()
    r = await p.invoke({
        "action": "write", "path": "../escape.md", "content": "x",
    })
    assert r["ok"] is False
    assert "越界" in r["error"]
