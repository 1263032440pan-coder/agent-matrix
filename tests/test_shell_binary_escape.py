"""Shell A1b：受控后缀重定向拒绝 + python 逃逸还原。"""

from __future__ import annotations

import pytest


@pytest.fixture
def sh_ws(tmp_path, monkeypatch):
    root = tmp_path / "tasks" / "1" / "1"
    root.mkdir(parents=True)
    import project_runtime.worker.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_WORKSPACE_ROOT", (tmp_path / "tasks").resolve())
    monkeypatch.chdir(tmp_path)
    return root


@pytest.mark.asyncio
async def test_redirect_pptx_denied(sh_ws):
    from project_runtime.worker.tools import ShellProvider

    r = await ShellProvider().invoke({
        "command": "echo hi > foo.pptx",
        "workspace": str(sh_ws),
    })
    assert r["ok"] is False
    assert r.get("error_code") == "BINARY_SUFFIX_DENIED"


@pytest.mark.asyncio
async def test_python_write_pptx_restored(sh_ws):
    from project_runtime.worker.tools import ShellProvider

    (sh_ws / "mk_pptx.py").write_text(
        "open('escape.pptx','w',encoding='utf-8').write('fake')\n",
        encoding="utf-8",
    )
    r = await ShellProvider().invoke({
        "command": "python mk_pptx.py",
        "workspace": str(sh_ws),
    })
    assert r["ok"] is False
    assert r.get("error_code") == "BINARY_WRITE_ESCAPE"
    assert not (sh_ws / "escape.pptx").exists()
