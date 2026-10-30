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


def test_win_shell_translate_grep_find():
    from project_runtime.worker.tools import translate_win_shell

    cmd, args = translate_win_shell("grep", "-n hello a.txt")
    assert cmd == "findstr"
    assert "/N" in args
    assert '/C:"hello"' in args
    cmd, args = translate_win_shell("find", ". -name *.java")
    assert cmd == "dir"
    assert "/s" in args and "*.java" in args
    cmd, args = translate_win_shell("ls", "-la src")
    assert cmd == "dir" and args == "src"


def test_win_text_cmd_head_tail_wc(tmp_path):
    from project_runtime.worker.tools import run_win_text_cmd

    f = tmp_path / "a.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    h = run_win_text_cmd("head", "-n 2 a.txt", tmp_path)
    assert h["ok"] and h["stdout"].startswith("a\nb")
    t = run_win_text_cmd("tail", "-n 1 a.txt", tmp_path)
    assert t["ok"] and t["stdout"].strip() == "c"
    w = run_win_text_cmd("wc", "-l a.txt", tmp_path)
    assert w["ok"] and w["stdout"] == "3"


def test_aggregate_tool_quality():
    from apps.runtime.api.projects import _aggregate_tool_quality

    rows = [
        ("LLM_TOOL_RESULT", '{"error_code":"TRUNCATED"}'),
        ("LLM_TOOL_RESULT", '{"error_code":"TRUNCATED"}'),
        ("LLM_TOOL_RESULT", '{"ok":true}'),
        ("LLM_TOOL_CALL", '{"repaired":true}'),
        ("LLM_TOOL_CALL", '{"repaired":false}'),
    ]
    q = _aggregate_tool_quality(rows)
    assert q["error_codes"]["TRUNCATED"] == 2
    assert q["truncated"] == 1
    assert q["repaired"] == 1


def test_verification_level_compile(tmp_path, monkeypatch):
    from apps.runtime.api.projects import _verification_level

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "tasks" / "2" / "15"
    root.mkdir(parents=True)
    (root / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert _verification_level(2, 15) == "compile"
    (root / "pom.xml").unlink()
    (root / "app.py").write_text("x=1", encoding="utf-8")
    assert _verification_level(2, 15) == "syntax"
