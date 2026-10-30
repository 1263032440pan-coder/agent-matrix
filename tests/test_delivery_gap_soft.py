"""SYS-138/139: 预算用尽/返工用尽 → 软放行载荷（非 FAILED）。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.procedure.token_stop import (
    check_obligation_tokens,
    check_obligation_tokens_detailed,
)
from project_runtime.worker.llm_worker import DeliveryGapSoftAcceptError


def test_detailed_stop_returns_unmet_and_scan_root(tmp_path: Path):
    root = tmp_path / "26"
    root.mkdir()
    tokens = [
        {
            "id": "prefix:d1:readme",
            "weight": 2,
            "deliverable_id": "d1",
            "label": "readme",
        },
        {
            "id": "ext:d1:any",
            "weight": 1,
            "deliverable_id": "d1",
            "exts": [".py", ".java"],
        },
    ]
    detailed = check_obligation_tokens_detailed(
        root, tokens, scan_path=str(root),
    )
    assert detailed.problems
    assert detailed.scan_root == "task"
    assert detailed.unmet
    kinds = {u.get("kind") for u in detailed.unmet}
    assert "prefix" in kinds or "ext" in kinds
    # 兼容旧 API
    assert check_obligation_tokens(root, tokens) == detailed.problems


def test_delivery_gap_soft_accept_carries_payload():
    gap = {
        "reason": "DELIVERY_GAP_AFTER_BUDGET",
        "soft_accepted": True,
        "problems": ["令牌缺前缀文件：readme*"],
        "unmet": [{"token_id": "prefix:d1:readme", "kind": "prefix", "hint": "x"}],
        "scan_root": "task",
    }
    exc = DeliveryGapSoftAcceptError("budget gap", delivery_gap=gap)
    assert exc.delivery_gap["soft_accepted"] is True
    assert exc.delivery_gap["reason"] == "DELIVERY_GAP_AFTER_BUDGET"
