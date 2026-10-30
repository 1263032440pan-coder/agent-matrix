"""SYS-158/159: 计划清单覆盖校验 + builder 新 kind / 缺工具链。"""

from __future__ import annotations

import pytest

from project_runtime.worker.obligation_feedback import planned_paths_cover

TOKENS = [
    {"id": "prefix:d1:readme", "label": "readme"},
    {"id": "ext:d1:any", "exts": [".java", ".md"]},
]


def test_planned_paths_missing_readme():
    r = planned_paths_cover(["src/App.java"], TOKENS)
    assert not r.ok()
    assert any("readme" in p.lower() for p in r.problems)


def test_planned_paths_cover_ok():
    r = planned_paths_cover(["README.md", "src/App.java"], TOKENS)
    assert r.ok()


@pytest.mark.asyncio
async def test_builder_missing_kind_toolchain(monkeypatch, tmp_path):
    from project_runtime.worker.coding_tools import BuilderProvider

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data/tasks").mkdir(parents=True)
    monkeypatch.setattr(
        "shutil.which", lambda name: None if name in ("mvn", "tsc") else "/bin/true",
    )
    p = BuilderProvider()
    r = await p.invoke({"kind": "mvn_compile", "workspace": "data/tasks"})
    assert r["ok"] is False
    assert r["error_code"] == "TOOLCHAIN_MISSING"
    assert "hint" in r
