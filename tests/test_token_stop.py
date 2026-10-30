"""SYS-123: 令牌 Stop 扫本任务目录；兄弟目录不能冒充。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.procedure.token_stop import check_obligation_tokens


def test_token_stop_passes_local_files(tmp_path: Path):
    task = tmp_path / "1"
    task.mkdir()
    (task / "a.py").write_text("x")
    (task / "b.md").write_text("x")
    (task / "c.toml").write_text("x")
    (task / "d.json").write_text("x")
    tokens = [
        {"id": "ext:d1:.py", "weight": 1},
        {"id": "ext:d1:.md", "weight": 1},
        {"id": "ext:d1:.toml", "weight": 1},
        {"id": "ext:d1:.json", "weight": 1},
        {"id": "count:d1:1", "weight": 1, "quota": 4},
    ]
    assert check_obligation_tokens(task, tokens) == []


def test_sibling_dir_cannot_satisfy_tokens(tmp_path: Path):
    a = tmp_path / "1"
    b = tmp_path / "2"
    a.mkdir()
    b.mkdir()
    (b / "readme.md").write_text("x")
    (b / "a.py").write_text("x")
    tokens = [
        {"id": "prefix:d1:readme", "weight": 2, "label": "readme"},
        {"id": "ext:d1:.py", "weight": 1},
    ]
    problems = check_obligation_tokens(a, tokens)
    assert problems
    assert check_obligation_tokens(b, tokens) == []


def test_count_inherits_ext_when_no_ext_token(tmp_path: Path):
    task = tmp_path / "1"
    task.mkdir()
    (task / "readme.md").write_text("x")
    (task / "x.py").write_text("1")
    (task / "y.py").write_text("2")
    (task / "z.py").write_text("3")
    (task / "w.py").write_text("4")
    tokens = [
        {"id": "prefix:d1:readme", "weight": 2, "label": "readme"},
        {"id": "count:d1:2", "weight": 1, "quota": 4},
    ]
    deliverables = {
        "d1": {"id": "d1", "match": {"ext": [".py", ".md"]}},
    }
    assert check_obligation_tokens(
        task, tokens, deliverables_by_id=deliverables,
    ) == []
