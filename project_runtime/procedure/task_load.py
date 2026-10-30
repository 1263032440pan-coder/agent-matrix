"""计划期义务令牌与负荷（SYS-118）——确定性、与 skill 正交。"""

from __future__ import annotations

import math
from typing import Any

FILES_PER_TOKEN = 4
Ocap = 8
Pcap = 8
LOAD_MAX = 6
BIN_CAPACITY = LOAD_MAX - 1  # 5


def token_weight(token_id: str) -> int:
    if token_id.startswith("prefix:"):
        return 2
    return 1


def expand_obligation_tokens(
    *,
    deliverable_ids: list[str],
    deliverables_by_id: dict[str, dict],
    stage_outputs: list[str] | None = None,
    feature_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    """从认领清单项 + stage_outputs + 可选功能点展开令牌。

    返回 list[{"id", "weight", "quota"? , "label"?}]。
    子节点 load 只应用划分到的令牌，勿对子节点再按整份 match 展开。
    """
    tokens: list[dict[str, Any]] = []
    for did in deliverable_ids:
        item = deliverables_by_id.get(did) or {}
        match = item.get("match") or {}
        if not isinstance(match, dict):
            match = {}

        # match.ext 与 check_match 对齐：多扩展名合成一枚「任一即可」
        # （旧版每种语言一枚 AND 令牌会逼 Java 工程再交 .go）
        norm_exts: list[str] = []
        for ext in match.get("ext") or []:
            e = str(ext).strip().lower()
            if not e:
                continue
            if not e.startswith("."):
                e = f".{e}"
            if e not in norm_exts:
                norm_exts.append(e)
        if norm_exts:
            tokens.append({
                "id": f"ext:{did}:any",
                "weight": 1,
                "deliverable_id": did,
                "exts": norm_exts,
                "label": ",".join(norm_exts),
            })

        for pref in match.get("any_name_prefix") or []:
            p = str(pref).strip()
            if not p:
                continue
            tokens.append({
                "id": f"prefix:{did}:{p}",
                "weight": 2,
                "deliverable_id": did,
                "label": p,
            })

        slot = str(match.get("slot") or "").strip()
        if slot:
            rel = str(match.get("rel_dir") or f"deliverables/{slot}").strip()
            tokens.append({
                "id": f"slot:{did}:{slot}",
                "weight": 1,
                "deliverable_id": did,
                "label": rel.replace("\\", "/").strip("/") + "/",
            })
        else:
            # SYS-134: 有槽不再展 name:；无槽保留旧 name_contains 令牌
            for needle in match.get("name_contains") or []:
                n = str(needle).strip()
                if not n:
                    continue
                tokens.append({
                    "id": f"name:{did}:{n}",
                    "weight": 1,
                    "deliverable_id": did,
                    "label": n,
                })

        min_count = int(match.get("min_count") or 1)
        if min_count < 1:
            min_count = 1
        n_bins = max(1, math.ceil(min_count / FILES_PER_TOKEN))
        for i in range(1, n_bins + 1):
            if i < n_bins:
                quota = FILES_PER_TOKEN
            else:
                quota = min_count - (n_bins - 1) * FILES_PER_TOKEN
            tokens.append({
                "id": f"count:{did}:{i}",
                "weight": 1,
                "deliverable_id": did,
                "quota": quota,
            })

    # 有认领清单项时，义务以 match 展开的令牌为准；再叠整表 stage_outputs
    # 会把无关文案（如「项目源码」）打进交付分片，误导 Worker 且抬高 load。
    if not deliverable_ids:
        for idx, so in enumerate((stage_outputs or [])[:Ocap]):
            label = str(so).strip()
            if not label:
                continue
            tokens.append({
                "id": f"output:{idx}",
                "weight": 1,
                "label": label,
            })

    for idx, feat in enumerate((feature_hints or [])[:Pcap]):
        label = str(feat).strip()
        if not label:
            continue
        tokens.append({
            "id": f"feature:{idx}",
            "weight": 1,
            "label": label,
        })

    return tokens


def compute_task_load(tokens: list[dict[str, Any]] | None) -> int:
    total = 0
    for t in tokens or []:
        if "weight" in t:
            total += int(t["weight"])
        else:
            total += token_weight(str(t.get("id") or ""))
    return total


def assert_token_weights_publishable() -> None:
    """发布约束：任意单枚权重 < LOAD_MAX。"""
    for sample in (
        "ext:x:.py", "ext:x:any", "prefix:x:readme", "name:x:a",
        "slot:x:requirements", "count:x:1", "output:0", "feature:0",
    ):
        if token_weight(sample) >= LOAD_MAX:
            raise AssertionError(
                f"token weight {token_weight(sample)} >= LOAD_MAX"
            )
