"""SYS-118: 义务令牌展开与 load 计算。"""

from __future__ import annotations

from project_runtime.procedure.task_load import (
    BIN_CAPACITY,
    FILES_PER_TOKEN,
    LOAD_MAX,
    Ocap,
    compute_task_load,
    expand_obligation_tokens,
    token_weight,
)


def test_single_token_weight_under_load_max():
    assert token_weight("ext:d1:.py") == 1
    assert token_weight("ext:d1:any") == 1
    assert token_weight("prefix:d1:readme") == 2
    assert token_weight("name:d1:测试") == 1
    assert token_weight("count:d1:1") == 1
    assert token_weight("output:0") == 1
    assert token_weight("feature:0") == 1
    for kind in ("ext", "prefix", "name", "count", "output", "feature"):
        w = 2 if kind == "prefix" else 1
        assert w < LOAD_MAX


def test_ext_list_collapses_to_single_any_token():
    """match.ext 与 check_match 对齐：多扩展名 → 一枚 ext:did:any（任一即可）。"""
    deliverables = {
        "d1": {
            "id": "d1",
            "name": "项目源码",
            "match": {
                "ext": [".py", ".go", ".java", ".rs"],
                "any_name_prefix": ["readme"],
                "min_count": 1,
            },
        },
    }
    tokens = expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )
    ext_toks = [t for t in tokens if str(t["id"]).startswith("ext:")]
    assert len(ext_toks) == 1
    assert ext_toks[0]["id"] == "ext:d1:any"
    assert ext_toks[0]["exts"] == [".py", ".go", ".java", ".rs"]
    assert compute_task_load(tokens) == 4  # any+prefix+count


def test_section_42_sample_triggers_overload():
    """ext:any(1) + readme(2) + min_count=16→4×count → load=7 ≥ 6。"""
    deliverables = {
        "d1": {
            "id": "d1",
            "name": "项目源码",
            "match": {
                "ext": [".py", ".md", ".toml", ".json"],
                "any_name_prefix": ["readme"],
                "min_count": 16,
            },
        },
    }
    tokens = expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )
    assert compute_task_load(tokens) == 7
    assert compute_task_load(tokens) >= LOAD_MAX
    ids = [t["id"] for t in tokens]
    assert sum(1 for i in ids if i.startswith("ext:")) == 1
    assert "ext:d1:any" in ids
    assert "prefix:d1:readme" in ids
    assert "count:d1:1" in ids and "count:d1:4" in ids
    assert tokens_by_id(tokens, "count:d1:1")["quota"] == FILES_PER_TOKEN
    assert tokens_by_id(tokens, "count:d1:4")["quota"] == FILES_PER_TOKEN


def test_skinny_match_does_not_overload():
    deliverables = {
        "d1": {
            "id": "d1",
            "name": "代码",
            "match": {
                "ext": [".py"],
                "any_name_prefix": ["readme"],
                "min_count": 1,
            },
        },
    }
    tokens = expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )
    assert compute_task_load(tokens) == 4
    assert compute_task_load(tokens) < LOAD_MAX
    assert tokens_by_id(tokens, "ext:d1:any")["exts"] == [".py"]


def test_code_stage_and_contract_do_not_affect_load():
    deliverables = {
        "d1": {
            "id": "d1",
            "name": "X",
            "match": {"ext": [".py"], "min_count": 1},
        },
    }
    tokens = expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )
    base = compute_task_load(tokens)
    assert compute_task_load(tokens) == base


def test_stage_outputs_capped_by_ocap():
    outs = [f"out{i}" for i in range(20)]
    tokens = expand_obligation_tokens(
        deliverable_ids=[],
        deliverables_by_id={},
        stage_outputs=outs,
        feature_hints=[],
    )
    assert sum(1 for t in tokens if t["id"].startswith("output:")) == Ocap


def test_min_count_5_splits_quota_4_and_1():
    deliverables = {
        "d1": {
            "id": "d1",
            "name": "X",
            "match": {"ext": [".py"], "min_count": 5},
        },
    }
    tokens = expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )
    assert tokens_by_id(tokens, "count:d1:1")["quota"] == 4
    assert tokens_by_id(tokens, "count:d1:2")["quota"] == 1


def test_bin_capacity_equals_load_max_minus_one():
    assert BIN_CAPACITY == LOAD_MAX - 1


def tokens_by_id(tokens: list[dict], tid: str) -> dict:
    for t in tokens:
        if t["id"] == tid:
            return t
    raise AssertionError(f"missing {tid}")
