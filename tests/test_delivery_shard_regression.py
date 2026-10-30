"""Regression: 交付-分片1 — count 与无关 output 混装 / 无槽提示."""

from __future__ import annotations

from project_runtime.procedure.plan_decompose import bin_pack_tokens
from project_runtime.procedure.task_load import expand_obligation_tokens
from project_runtime.procedure.token_stop import slot_dirs_for_tokens


def _slot_items():
    return {
        "d4": {
            "id": "d4",
            "name": "系统原型图",
            "match": {
                "slot": "prototypes",
                "rel_dir": "deliverables/prototypes",
                "min_count": 1,
            },
        },
        "d9": {
            "id": "d9",
            "name": "交付文档",
            "match": {
                "slot": "delivery",
                "rel_dir": "deliverables/delivery",
                "min_count": 1,
            },
        },
        "d10": {
            "id": "d10",
            "name": "使用文档",
            "match": {
                "slot": "user_guide",
                "rel_dir": "deliverables/user_guide",
                "min_count": 1,
            },
        },
    }


def test_expand_skips_stage_outputs_when_deliverable_ids_present():
    """有认领清单项时义务以 match 令牌为准，不再叠整表 stage_outputs."""
    toks = expand_obligation_tokens(
        deliverable_ids=["d4", "d9", "d10"],
        deliverables_by_id=_slot_items(),
        stage_outputs=[
            "项目源码",
            "解决方案",
            "需求文档",
            "系统原型图",
            "系统流程图",
            "系统架构文档",
            "开发文档",
            "测试用例",
        ],
    )
    ids = [t["id"] for t in toks]
    assert not any(i.startswith("output:") for i in ids)
    assert any(i.startswith("slot:d4:") for i in ids)
    assert any(i.startswith("count:d4:") for i in ids)


def test_expand_keeps_stage_outputs_when_no_deliverable_ids():
    toks = expand_obligation_tokens(
        deliverable_ids=[],
        deliverables_by_id={},
        stage_outputs=["任务图", "依赖边"],
    )
    assert [t["label"] for t in toks if t["id"].startswith("output:")] == [
        "任务图",
        "依赖边",
    ]


def test_slot_dirs_from_count_tokens_without_slot_token():
    """分片只有 count: 时也要能推出必写槽目录（SYS-137 补洞）."""
    tokens = [
        {"id": "count:d4:1", "weight": 1, "deliverable_id": "d4", "quota": 1},
        {"id": "count:d9:1", "weight": 1, "deliverable_id": "d9", "quota": 1},
        {"id": "output:0", "weight": 1, "label": "项目源码"},
    ]
    dirs = slot_dirs_for_tokens(tokens, _slot_items())
    assert "deliverables/prototypes/" in dirs
    assert "deliverables/delivery/" in dirs


def test_bin_pack_prefers_same_deliverable_id_affinity():
    tokens = expand_obligation_tokens(
        deliverable_ids=["d4", "d9", "d10"],
        deliverables_by_id=_slot_items(),
        stage_outputs=[],
    )
    bins = bin_pack_tokens(tokens)
    # 同一 did 的 slot+count 尽量同箱
    for b in bins:
        by_did: dict[str, list[str]] = {}
        for t in b:
            did = str(t.get("deliverable_id") or "")
            if not did:
                continue
            by_did.setdefault(did, []).append(t["id"])
        for did, ids in by_did.items():
            kinds = {i.split(":")[0] for i in ids}
            if "slot" in kinds and "count" in kinds:
                pass  # good
            # 若同箱只有其一也允许（容量不够），但不要求失败
    # 硬断言：至少有一个箱子同时含某 did 的 slot 与 count
    found = False
    for b in bins:
        ids = [t["id"] for t in b]
        for did in ("d4", "d9", "d10"):
            if (
                any(i.startswith(f"slot:{did}:") for i in ids)
                and any(i.startswith(f"count:{did}:") for i in ids)
            ):
                found = True
    assert found, bins
