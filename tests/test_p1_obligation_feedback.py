"""SYS-152～157: 缺口回执 / 可读清单 / 诊断 / nudge / append_file。"""

from __future__ import annotations

import pytest

from project_runtime.procedure.token_stop import check_obligation_tokens_detailed
from project_runtime.worker.obligation_feedback import (
    describe_obligation_tokens,
    format_gap_receipt,
    repair_nudge,
    tool_failure_diagnosis,
    workspace_tree,
)
from project_runtime.worker.tools import (
    AppendFileProvider,
    WriteFileProvider,
    expand_tool_whitelist,
)

TOKENS = [
    {
        "id": "ext:d1:any",
        "exts": [".py", ".ts", ".js", ".go", ".java", ".rs", ".md"],
    },
    {"id": "prefix:d1:readme", "label": "readme"},
    {"id": "count:d1:1", "quota": 1},
]


def test_describe_tokens_has_example_paths():
    lines = describe_obligation_tokens(TOKENS)
    joined = "\n".join(lines)
    assert "README.md" in joined
    assert ".java" in joined
    assert "readme" in joined.lower()


def test_workspace_tree_lists_relative_paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.java").write_text("x", encoding="utf-8")
    (tmp_path / "pom.xml").write_text("<p/>", encoding="utf-8")
    tree = workspace_tree(tmp_path)
    assert "src/A.java" in tree.replace("\\", "/")
    assert "pom.xml" in tree


def test_gap_receipt_before_and_after_readme(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    before = check_obligation_tokens_detailed(tmp_path, TOKENS)
    text = format_gap_receipt(before)
    assert "剩余缺口" in text and "readme" in text.lower()
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    after = check_obligation_tokens_detailed(tmp_path, TOKENS)
    assert "全部满足" in format_gap_receipt(after)


def test_repair_nudge_escalates():
    problems = ["令牌缺前缀文件：readme*"]
    stats = {"MISSING_FIELD": 18}
    n1 = repair_nudge(0, problems, error_stats=stats)
    n2 = repair_nudge(1, problems, error_stats=stats)
    n3 = repair_nudge(2, problems, error_stats=stats)
    assert "readme" in n1.lower()
    assert "MISSING_FIELD" in n2 and "示例" in n2
    assert "最小动作" in n3 or "write_file" in n3
    assert n1 != n2 != n3


def test_diagnosis_after_three_failures():
    fails = [
        {"tool": "tool:write_file", "error_code": "MISSING_FIELD", "path": ""},
        {"tool": "tool:write_file", "error_code": "MISSING_FIELD", "path": ""},
        {"tool": "tool:write_file", "error_code": "MISSING_FIELD", "path": ""},
    ]
    msg = tool_failure_diagnosis(fails)
    assert msg is not None
    assert "MISSING_FIELD" in msg
    assert "配额" in msg
    assert tool_failure_diagnosis(fails[:2]) is None


def test_expand_alias_includes_append():
    assert "append_file" in expand_tool_whitelist(["file_rw"])


@pytest.mark.asyncio
async def test_append_file_concatenates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = "data/tasks/2/15"
    (tmp_path / ws).mkdir(parents=True)
    payload = {"path": "src/A.java", "content": "class A {", "__ws_root": ws}
    r1 = await WriteFileProvider().invoke(payload)
    assert r1["ok"]
    r2 = await AppendFileProvider().invoke({
        "path": "src/A.java", "content": "}", "__ws_root": ws,
    })
    assert r2["ok"]
    text = (tmp_path / ws / "src" / "A.java").read_text(encoding="utf-8")
    assert text == "class A {}"


@pytest.mark.asyncio
async def test_append_creates_and_forbids_validator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = "data/tasks/2/15"
    (tmp_path / ws).mkdir(parents=True)
    r = await AppendFileProvider().invoke({
        "path": "README.md", "content": "# t", "__ws_root": ws,
    })
    assert r["ok"] is True
    from project_runtime.procedure.delivery_validator import SCRIPT_NAME

    ban = await AppendFileProvider().invoke({
        "path": SCRIPT_NAME, "content": "x", "__ws_root": ws,
    })
    assert ban["ok"] is False
    assert ban["error_code"] == "FORBIDDEN_PATH"
