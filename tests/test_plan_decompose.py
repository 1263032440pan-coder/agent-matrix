"""SYS-120/121/126: 计划期过载拆分片级硬门与确定性装箱。"""

from __future__ import annotations

import pytest

from project_runtime.procedure.plan_decompose import (
    BIN_CAPACITY,
    DECOMPOSE_NODE_RETRY,
    DECOMPOSE_SPLIT_BUDGET,
    LOAD_MAX,
    apply_replace_in_graph,
    bin_pack_tokens,
    deterministic_subs,
    validate_shard,
)
from project_runtime.procedure.task_load import (
    compute_task_load,
    expand_obligation_tokens,
)


def _sample_tokens():
    """过载样本：ext:any(1)+readme(2)+min_count=16→4×count → load=7。"""
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
    return expand_obligation_tokens(
        deliverable_ids=["d1"],
        deliverables_by_id=deliverables,
        stage_outputs=[],
        feature_hints=[],
    )


def test_constants():
    assert LOAD_MAX == 6
    assert BIN_CAPACITY == 5
    assert DECOMPOSE_NODE_RETRY == 2
    assert DECOMPOSE_SPLIT_BUDGET == 16


def test_bin_pack_section_42_sample():
    tokens = _sample_tokens()
    assert compute_task_load(tokens) == 7
    bins = bin_pack_tokens(tokens)
    assert len(bins) >= 2
    for b in bins:
        assert compute_task_load(b) <= BIN_CAPACITY
    flat = [t["id"] for b in bins for t in b]
    assert sorted(flat) == sorted(t["id"] for t in tokens)


def test_deterministic_subs_puts_ids_on_tail():
    tokens = _sample_tokens()
    node = {
        "task_key": "Worker执行",
        "role": "implement",
        "task_type": "开发实现",
        "code_stage": True,
        "deliverable_ids": ["d1"],
        "obligation_tokens": tokens,
        "stage_outputs": [],
        "contract": "do it",
    }
    subs = deterministic_subs(node)
    assert len(subs) >= 2
    assert subs[-1]["deliverable_ids"] == ["d1"]
    for s in subs[:-1]:
        assert s["deliverable_ids"] == []
    for s in subs:
        assert compute_task_load(s["obligation_tokens"]) < LOAD_MAX
        assert s["role"] == "implement"
        assert s["code_stage"] is True


def test_validate_shard_accepts_section_42_manual_split():
    tokens = _sample_tokens()
    by_id = {t["id"]: t for t in tokens}
    t_node = {
        "task_key": "Worker执行",
        "role": "implement",
        "deliverable_ids": ["d1"],
        "obligation_tokens": tokens,
        "status": "CREATED",
    }
    g = {
        "nodes": {"Worker执行": t_node, "理解": {
            "task_key": "理解",
            "role": "process",
            "deliverable_ids": [],
            "obligation_tokens": [],
            "status": "CREATED",
        }},
        "deps": [("Worker执行", "理解")],
        "replaced": {},
    }
    subs = [
        {
            "task_key": "实现-数据层",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": [
                by_id["ext:d1:any"],
                by_id["count:d1:1"],
                by_id["count:d1:2"],
                by_id["count:d1:3"],
            ],
            "contract": "ext+counts",
        },
        {
            "task_key": "实现-收口",
            "role": "implement",
            "deliverable_ids": ["d1"],
            "obligation_tokens": [
                by_id["prefix:d1:readme"],
                by_id["count:d1:4"],
            ],
            "contract": "readme+count4",
        },
    ]
    assert compute_task_load(subs[0]["obligation_tokens"]) == 4
    assert compute_task_load(subs[1]["obligation_tokens"]) == 3
    problems = validate_shard(
        replace="Worker执行",
        T=t_node,
        subs=subs,
        G=g,
        categories={"d1": "source"},
    )
    assert problems == []


def test_validate_shard_rejects_lost_token_or_id():
    tokens = _sample_tokens()
    by_id = {t["id"]: t for t in tokens}
    t_node = {
        "task_key": "Worker执行",
        "role": "implement",
        "deliverable_ids": ["d1"],
        "obligation_tokens": tokens,
        "status": "CREATED",
    }
    g = {
        "nodes": {"Worker执行": t_node},
        "deps": [],
        "replaced": {},
    }
    # drop one token
    bad = [
        {
            "task_key": "A",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": [by_id["ext:d1:any"]],
        },
        {
            "task_key": "B",
            "role": "implement",
            "deliverable_ids": ["d1"],
            "obligation_tokens": [by_id["prefix:d1:readme"]],
        },
    ]
    problems = validate_shard(
        replace="Worker执行", T=t_node, subs=bad, G=g,
        categories={"d1": "source"},
    )
    assert any("令牌" in p for p in problems)

    # drop id
    half = tokens[:4]
    rest = tokens[4:]
    bad2 = [
        {
            "task_key": "A",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": half,
        },
        {
            "task_key": "B",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": rest,
        },
    ]
    problems2 = validate_shard(
        replace="Worker执行", T=t_node, subs=bad2, G=g,
        categories={"d1": "source"},
    )
    assert any("id" in p.lower() or "主责" in p for p in problems2)


def test_validate_shard_rejects_empty_sub():
    tokens = _sample_tokens()[:2]
    t_node = {
        "task_key": "T",
        "role": "implement",
        "deliverable_ids": [],
        "obligation_tokens": tokens,
        "status": "CREATED",
    }
    g = {"nodes": {"T": t_node}, "deps": [], "replaced": {}}
    subs = [
        {
            "task_key": "A",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": tokens,
        },
        {
            "task_key": "B",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": [],
        },
    ]
    problems = validate_shard(
        replace="T", T=t_node, subs=subs, G=g, categories={},
    )
    assert any("空" in p or "令牌" in p for p in problems)


@pytest.mark.asyncio
async def test_decompose_memory_graph_binpack_when_llm_fails():
    from project_runtime.procedure.plan_decompose import (
        decompose_memory_graph,
    )

    tokens = _sample_tokens()
    g = {
        "nodes": {
            "Worker执行": {
                "task_key": "Worker执行",
                "role": "implement",
                "task_type": "开发实现",
                "code_stage": True,
                "deliverable_ids": ["d1"],
                "obligation_tokens": tokens,
                "stage_outputs": [],
                "status": "CREATED",
                "frozen": False,
            },
        },
        "deps": [],
        "replaced": {},
    }
    manifest = {
        "deliverables": [
            {"id": "d1", "name": "项目源码", "category": "source",
             "match": {
                 "ext": [".py", ".md", ".toml", ".json"],
                 "any_name_prefix": ["readme"],
                 "min_count": 8,
             }},
        ],
    }

    async def bad_llm(_prompt: str) -> str:
        return "not json"

    stats = await decompose_memory_graph(
        g,
        manifest=manifest,
        categories={"d1": "source"},
        llm=bad_llm,
    )
    assert stats["bin_packed"] >= 1
    assert "Worker执行" not in g["nodes"]
    for nd in g["nodes"].values():
        assert compute_task_load(nd["obligation_tokens"]) < LOAD_MAX
    owners = [
        k for k, nd in g["nodes"].items()
        if "d1" in nd.get("deliverable_ids", [])
    ]
    assert len(owners) == 1


def test_apply_replace_no_cycle_when_verify_precedes_later_implement():
    """质量关卡(verify)→返修(implement) 已有边时，不得再加反向边成环。"""
    from project_runtime.procedure.plan_decompose import (
        apply_replace_in_graph,
        deterministic_subs,
    )

    tokens = _sample_tokens()
    # inflate with outputs so deterministic may split ≥2
    g = {
        "nodes": {
            "理解": {
                "task_key": "理解", "role": "process",
                "deliverable_ids": [], "obligation_tokens": [],
                "status": "CREATED",
            },
            "Worker执行": {
                "task_key": "Worker执行", "role": "implement",
                "deliverable_ids": ["d1"], "obligation_tokens": tokens,
                "status": "CREATED", "code_stage": True,
                "task_type": "开发实现",
            },
            "质量关卡": {
                "task_key": "质量关卡", "role": "verify",
                "deliverable_ids": [], "obligation_tokens": [],
                "status": "CREATED",
            },
            "返修": {
                "task_key": "返修", "role": "implement",
                "deliverable_ids": [], "obligation_tokens": [],
                "status": "CREATED",
            },
            "测试": {
                "task_key": "测试", "role": "verify",
                "deliverable_ids": [], "obligation_tokens": [],
                "status": "CREATED",
            },
        },
        "deps": [
            ("Worker执行", "理解"),
            ("质量关卡", "Worker执行"),
            ("返修", "质量关卡"),
            ("测试", "返修"),
        ],
        "replaced": {},
    }
    subs = deterministic_subs(g["nodes"]["Worker执行"])
    apply_replace_in_graph(g, "Worker执行", subs)
    assert "Worker执行" not in g["nodes"]
    assert ("返修", "质量关卡") in g["deps"]


def test_apply_replace_wires_chain():
    tokens = _sample_tokens()
    by_id = {t["id"]: t for t in tokens}
    g = {
        "nodes": {
            "U": {
                "task_key": "U",
                "role": "process",
                "deliverable_ids": [],
                "obligation_tokens": [],
                "status": "CREATED",
            },
            "Worker执行": {
                "task_key": "Worker执行",
                "role": "implement",
                "deliverable_ids": ["d1"],
                "obligation_tokens": tokens,
                "status": "CREATED",
                "task_type": "开发实现",
                "code_stage": True,
            },
            "V": {
                "task_key": "V",
                "role": "verify",
                "deliverable_ids": [],
                "obligation_tokens": [],
                "status": "CREATED",
            },
        },
        "deps": [("Worker执行", "U"), ("V", "Worker执行")],
        "replaced": {},
    }
    subs = [
        {
            "task_key": "实现-数据层",
            "role": "implement",
            "deliverable_ids": [],
            "obligation_tokens": [
                by_id[k] for k in (
                    "ext:d1:any", "count:d1:1", "count:d1:2", "count:d1:3",
                )
            ],
            "contract": "a",
            "bin_packed": False,
        },
        {
            "task_key": "实现-收口",
            "role": "implement",
            "deliverable_ids": ["d1"],
            "obligation_tokens": [
                by_id["prefix:d1:readme"], by_id["count:d1:4"],
            ],
            "contract": "b",
            "bin_packed": False,
        },
    ]
    apply_replace_in_graph(g, "Worker执行", subs)
    assert "Worker执行" not in g["nodes"]
    assert "实现-数据层" in g["nodes"] and "实现-收口" in g["nodes"]
    assert ("实现-数据层", "U") in g["deps"]
    assert ("实现-收口", "实现-数据层") in g["deps"]
    assert ("V", "实现-收口") in g["deps"]
    assert g["replaced"]["Worker执行"] == ["实现-数据层", "实现-收口"]
