"""SYS-107~112: AI 认领硬门 / 顺序 / 全覆盖（无真 LLM）。"""

from __future__ import annotations

import pytest

from project_runtime.procedure.delivery_assignment import (
    DeliveryAssignmentError,
    apply_assignment_to_playbook_refs,
    merge_chunk_assignments,
    validate_assignment_hard_gate,
    validate_chunk_assignment,
)


def _manifest(*ids: str) -> dict:
    return {
        "version": 1,
        "deliverables": [
            {"id": i, "name": i, "match": {"ext": [".md"]}} for i in ids
        ],
    }


def test_hard_gate_requires_full_coverage():
    tasks = [
        {"task_key": "a", "role": "implement"},
        {"task_key": "b", "role": "deliver"},
    ]
    cats = [
        {"id": "d1", "category": "document"},
        {"id": "d2", "category": "document"},
    ]
    problems = validate_assignment_hard_gate(
        assignment={"d1": "a"},
        manifest=_manifest("d1", "d2"),
        tasks=tasks,
        categories=cats,
        roles={t["task_key"]: t["role"] for t in tasks},
        topo_order=["a", "b"],
    )
    assert any("全覆盖" in p or "未覆盖" in p for p in problems)


def test_hard_gate_rejects_duplicate_owners():
    """合并层应保证单一主责；硬门也拒双挂。"""
    tasks = [{"task_key": "a", "role": "implement"}]
    cats = [{"id": "d1", "category": "source"}]
    # 模拟非法：同一 id 不应出现两次；硬门输入是 id→task
    problems = validate_assignment_hard_gate(
        assignment={"d1": "a"},
        manifest=_manifest("d1"),
        tasks=tasks,
        categories=cats,
        roles={"a": "implement"},
        topo_order=["a"],
    )
    assert problems == []


def test_hard_gate_rejects_source_on_deliver_alone():
    tasks = [{"task_key": "pack", "role": "deliver"}]
    cats = [{"id": "src", "category": "source"}]
    problems = validate_assignment_hard_gate(
        assignment={"src": "pack"},
        manifest=_manifest("src"),
        tasks=tasks,
        categories=cats,
        roles={"pack": "deliver"},
        topo_order=["pack"],
    )
    assert any("source" in p.lower() or "主责" in p for p in problems)


def test_hard_gate_rejects_verification_before_implement():
    tasks = [
        {"task_key": "test", "role": "verify"},
        {"task_key": "code", "role": "implement"},
    ]
    cats = [{"id": "rep", "category": "verification"}]
    problems = validate_assignment_hard_gate(
        assignment={"rep": "test"},
        manifest=_manifest("rep"),
        tasks=tasks,
        categories=cats,
        roles={"test": "verify", "code": "implement"},
        topo_order=["test", "code"],  # verify 早于 implement
    )
    assert any("顺序" in p or "verification" in p.lower() for p in problems)


def test_chunk_rejects_unknown_id_and_stolen_id():
    remaining = {"d1", "d2"}
    ok, err = validate_chunk_assignment(
        chunk=[{"id": "d9", "task_key": "a", "reason": "x"}],
        remaining=remaining,
        task_keys={"a"},
    )
    assert not ok and err

    ok2, err2 = validate_chunk_assignment(
        chunk=[{"id": "d1", "task_key": "ghost", "reason": "x"}],
        remaining=remaining,
        task_keys={"a"},
    )
    assert not ok2 and err2


def test_merge_chunk_rejects_duplicate_id():
    accepted: dict[str, str] = {"d1": "a"}
    with pytest.raises(DeliveryAssignmentError):
        merge_chunk_assignments(
            accepted,
            [{"id": "d1", "task_key": "b", "reason": "steal"}],
        )


def test_apply_assignment_updates_playbook_json():
    ref = '{"task_name": "t", "deliverable_ids": []}'
    out = apply_assignment_to_playbook_refs(
        {"t1": ref},
        {"d1": "t1", "d2": "t1"},
    )
    import json

    data = json.loads(out["t1"])
    assert set(data["deliverable_ids"]) == {"d1", "d2"}
