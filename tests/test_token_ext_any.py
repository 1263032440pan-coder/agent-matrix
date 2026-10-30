"""ext 令牌与 check_match 对齐：同一 deliverable 多扩展名 = 任一即可。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.procedure.token_stop import check_obligation_tokens


def test_ext_tokens_same_deliverable_are_any_not_all(tmp_path: Path):
    """清单 match.ext 是过滤器（任一）；令牌 Stop 不得要求每种语言都有。"""
    root = tmp_path / "26"
    src = root / "deliverables" / "source"
    src.mkdir(parents=True)
    (src / "App.java").write_text("class App {}", encoding="utf-8")
    tokens = [
        {"id": "prefix:d1:readme", "weight": 2, "deliverable_id": "d1", "label": "readme"},
        {"id": "count:d1:1", "weight": 1, "deliverable_id": "d1", "quota": 1},
        {"id": "ext:d1:.go", "weight": 1, "deliverable_id": "d1"},
        {"id": "ext:d1:.java", "weight": 1, "deliverable_id": "d1"},
    ]
    deliverables = {
        "d1": {
            "id": "d1",
            "match": {
                "ext": [".py", ".go", ".java", ".rs"],
                "any_name_prefix": ["readme"],
                "slot": "source",
                "rel_dir": "deliverables/source",
                "min_count": 1,
            },
        },
    }
    # 有 java、无 go：ext 应过；仍缺 readme
    problems = check_obligation_tokens(
        root, tokens, deliverables_by_id=deliverables,
    )
    assert not any(".go" in p for p in problems), problems
    assert any("readme" in p for p in problems), problems

    (src / "readme.md").write_text("# ok", encoding="utf-8")
    assert check_obligation_tokens(
        root, tokens, deliverables_by_id=deliverables,
    ) == []


def test_ext_any_still_fails_when_none_match(tmp_path: Path):
    root = tmp_path / "1"
    root.mkdir()
    (root / "notes.txt").write_text("x", encoding="utf-8")
    tokens = [
        {"id": "ext:d1:.go", "weight": 1, "deliverable_id": "d1"},
        {"id": "ext:d1:.java", "weight": 1, "deliverable_id": "d1"},
    ]
    problems = check_obligation_tokens(root, tokens)
    assert any("扩展名" in p for p in problems)


def test_ext_any_token_from_expand(tmp_path: Path):
    root = tmp_path / "1"
    (root / "deliverables" / "source").mkdir(parents=True)
    (root / "deliverables" / "source" / "App.java").write_text(
        "class App {}", encoding="utf-8",
    )
    (root / "deliverables" / "source" / "readme.md").write_text(
        "# ok", encoding="utf-8",
    )
    tokens = [
        {
            "id": "ext:d1:any",
            "weight": 1,
            "deliverable_id": "d1",
            "exts": [".py", ".go", ".java", ".rs"],
        },
        {
            "id": "prefix:d1:readme",
            "weight": 2,
            "deliverable_id": "d1",
            "label": "readme",
        },
    ]
    assert check_obligation_tokens(root, tokens) == []
