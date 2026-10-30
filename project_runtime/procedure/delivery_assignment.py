"""交付清单 AI 认领：分次标注/分配 + Runtime 硬门（SYS-107~112）。

不特化产物类型；关键词表仅作标注缺失兜底，不作主判。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

ERROR_CODE = "DELIVERY_ASSIGNMENT_INVALID"
ASSIGN_REPAIR_MAX = 3
CHUNK_RETRY_MAX = 2

ROLES = frozenset({"process", "implement", "verify", "deliver"})
CATEGORIES = frozenset({
    "source", "verification", "document", "package", "other",
})

LlmFn = Callable[[str], Awaitable[str]]


class DeliveryAssignmentError(ValueError):
    def __init__(self, message: str, *, problems: list[str] | None = None):
        super().__init__(message)
        self.error_code = ERROR_CODE
        self.message = message
        self.problems = list(problems or [])


# ── 关键词兜底（仅标注缺失时）──────────────────────────────────────────

_ROLE_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"测试|验证|验收|qa", re.I), "verify"),
    (re.compile(r"打包|交付|汇总|集成|发布", re.I), "deliver"),
    (re.compile(r"开发|实现|编码|构建|编写", re.I), "implement"),
    (re.compile(r"理解|调研|方案|计划|分析|需求", re.I), "process"),
]
_CAT_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.(py|js|ts|go|java|rs)\b|源码|代码|入口", re.I), "source"),
    (re.compile(r"测试|报告|coverage|验收", re.I), "verification"),
    (re.compile(r"打包|zip|包|发行", re.I), "package"),
    (re.compile(r"文档|readme|说明|\.md\b|ppt", re.I), "document"),
]


def fallback_role(task_name: str) -> str:
    for pat, role in _ROLE_HINTS:
        if pat.search(task_name or ""):
            return role
    return "implement"


def fallback_category(item: dict) -> str:
    blob = " ".join(
        str(x) for x in [
            item.get("id"), item.get("name"),
            item.get("match"),
        ] if x
    )
    for pat, cat in _CAT_HINTS:
        if pat.search(blob):
            return cat
    return "other"


def _parse_json_object(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    brace = re.search(r"[\{\[][\s\S]*[\}\]]", raw)
    if not brace:
        raise DeliveryAssignmentError("模型未返回 JSON")
    try:
        return json.loads(brace.group(0))
    except json.JSONDecodeError as e:
        raise DeliveryAssignmentError(f"JSON 无法解析：{e}") from e


def validate_chunk_assignment(
    *,
    chunk: list[dict],
    remaining: set[str],
    task_keys: set[str],
) -> tuple[bool, str]:
    if not isinstance(chunk, list) or not chunk:
        return False, "片为空或非列表"
    seen: set[str] = set()
    for row in chunk:
        if not isinstance(row, dict):
            return False, "片内项非对象"
        did = str(row.get("id") or "").strip()
        tk = str(row.get("task_key") or "").strip()
        if not did or not tk:
            return False, "缺 id/task_key"
        if did not in remaining:
            return False, f"id 不在剩余集合：{did}"
        if did in seen:
            return False, f"片内重复 id：{did}"
        if tk not in task_keys:
            return False, f"未知 task_key：{tk}"
        seen.add(did)
    return True, ""


def merge_chunk_assignments(
    accepted: dict[str, str],
    chunk: list[dict],
) -> dict[str, str]:
    out = dict(accepted)
    for row in chunk:
        did = str(row["id"])
        if did in out:
            raise DeliveryAssignmentError(f"抢占已接受 id：{did}")
        out[did] = str(row["task_key"])
    return out


def validate_assignment_hard_gate(
    *,
    assignment: dict[str, str],
    manifest: dict,
    tasks: list[dict],
    categories: list[dict],
    roles: dict[str, str],
    topo_order: list[str],
) -> list[str]:
    """返回 problems；空列表 = 通过。"""
    problems: list[str] = []
    ids = [str(d["id"]) for d in (manifest.get("deliverables") or [])]
    id_set = set(ids)
    task_keys = {str(t["task_key"]) for t in tasks}

    for did, tk in assignment.items():
        if did not in id_set:
            problems.append(f"非法 id：{did}")
        if tk not in task_keys:
            problems.append(f"非法 task_key：{tk}")

    # 单一主责：assignment 已是 id→task；检查覆盖
    covered = set(assignment.keys())
    missing = [i for i in ids if i not in covered]
    if missing:
        problems.append(f"未覆盖（全覆盖失败）：{missing}")
    extra = covered - id_set
    if extra:
        problems.append(f"多余 id：{sorted(extra)}")

    cat_map = {
        str(c["id"]): str(c.get("category") or "other") for c in categories
    }
    for i in ids:
        if i not in cat_map:
            cat_map[i] = "other"

    for did, tk in assignment.items():
        cat = cat_map.get(did, "other")
        role = roles.get(tk, "implement")
        if cat == "source" and role in ("process", "deliver"):
            problems.append(
                f"一致性：source 项 {did} 不得由 {role} 节点 {tk} 单独主责"
            )

    # 顺序：verification 主责不早于任一 implement
    rank = {k: i for i, k in enumerate(topo_order)}
    implement_ranks = [
        rank[k] for k, r in roles.items()
        if r == "implement" and k in rank
    ]
    max_impl = max(implement_ranks) if implement_ranks else -1
    for did, tk in assignment.items():
        if cat_map.get(did) != "verification":
            continue
        if tk not in rank:
            problems.append(f"顺序：verification {did} 主责 {tk} 不在拓扑")
            continue
        if max_impl >= 0 and rank[tk] < max_impl:
            problems.append(
                f"顺序：verification 项 {did} 主责 {tk} 早于 implement 节点"
            )

    # package：不早于非 package 项的最晚主责
    non_pkg_ranks: list[int] = []
    for did, tk in assignment.items():
        if cat_map.get(did) == "package":
            continue
        if tk in rank:
            non_pkg_ranks.append(rank[tk])
    max_non_pkg = max(non_pkg_ranks) if non_pkg_ranks else -1
    for did, tk in assignment.items():
        if cat_map.get(did) != "package":
            continue
        if tk not in rank:
            problems.append(f"顺序：package {did} 主责 {tk} 不在拓扑")
            continue
        if max_non_pkg >= 0 and rank[tk] < max_non_pkg:
            problems.append(
                f"顺序：package 项 {did} 主责 {tk} 早于其打包对象主责"
            )

    return problems


def apply_assignment_to_playbook_refs(
    refs_by_task_key: dict[str, str],
    assignment: dict[str, str],
) -> dict[str, str]:
    """按 task_key 回写 deliverable_ids（单一主责 → 每节点 ids 列表）。"""
    by_task: dict[str, list[str]] = {k: [] for k in refs_by_task_key}
    for did, tk in assignment.items():
        by_task.setdefault(tk, []).append(did)
    out: dict[str, str] = {}
    for tk, ref in refs_by_task_key.items():
        try:
            data = json.loads(ref) if ref else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["deliverable_ids"] = sorted(by_task.get(tk, []))
        out[tk] = json.dumps(data, ensure_ascii=False)
    return out


def coerce_package_marked_source(
    deliverables: list[dict],
    categories: dict[str, dict],
) -> dict[str, dict]:
    """源码类名称却标成 package → 强制 source（§4.3 轻量纠偏）。"""
    from project_runtime.procedure.delivery_manifest import is_source_like_item

    out = {k: dict(v) for k, v in categories.items()}
    for d in deliverables:
        did = str(d.get("id") or "")
        if not did or did not in out:
            continue
        if out[did].get("category") != "package":
            continue
        if is_source_like_item(str(d.get("name") or ""), d.get("match") or {}):
            out[did]["category"] = "source"
            out[did]["reason"] = "runtime_coerce"
    return out


def deterministic_assign(
    *,
    manifest: dict,
    tasks: list[dict],
    topo_order: list[str],
    roles: dict[str, str],
    categories: dict[str, dict],
    frozen_assignment: dict[str, str] | None = None,
) -> dict[str, str]:
    """确定性兜底：构造必过 SYS-109/110 的 id→task_key（SYS-130）。"""
    deliverables = list(manifest.get("deliverables") or [])
    all_ids = [str(d["id"]) for d in deliverables]
    if not all_ids:
        return dict(frozen_assignment or {})
    if not topo_order:
        raise DeliveryAssignmentError(
            "无可分配任务（空拓扑）",
            problems=["empty topo"],
        )

    last = topo_order[-1]
    implements = [k for k in topo_order if roles.get(k) == "implement"]
    verifies = [k for k in topo_order if roles.get(k) == "verify"]
    delivers = [k for k in topo_order if roles.get(k) == "deliver"]
    non_process = [k for k in topo_order if roles.get(k) != "process"]

    source_anchor = (
        implements[-1] if implements
        else (non_process[-1] if non_process else last)
    )
    verify_anchor = verifies[-1] if verifies else last
    # verification 不得早于 implement
    if implements and verify_anchor in topo_order:
        if topo_order.index(verify_anchor) < max(
            topo_order.index(i) for i in implements
        ):
            verify_anchor = last
    package_anchor = delivers[-1] if delivers else last

    cat_of = {
        str(c): str((categories.get(c) or {}).get("category") or "other")
        for c in all_ids
    }
    # also accept categories as id→{category}
    for d in deliverables:
        did = str(d["id"])
        if did in categories and isinstance(categories[did], dict):
            cat_of[did] = str(categories[did].get("category") or "other")

    assignment: dict[str, str] = dict(frozen_assignment or {})
    load: dict[str, int] = {k: 0 for k in topo_order}
    for tk in assignment.values():
        if tk in load:
            load[tk] += 1

    def _place(did: str, tk: str) -> None:
        assignment[did] = tk
        load[tk] = load.get(tk, 0) + 1

    # order: source → document/other → verification → package
    buckets: dict[str, list[str]] = {
        "source": [], "document": [], "other": [],
        "verification": [], "package": [],
    }
    for did in all_ids:
        if did in assignment:
            continue
        cat = cat_of.get(did, "other")
        buckets.setdefault(cat if cat in buckets else "other", []).append(did)

    for did in buckets["source"]:
        _place(did, source_anchor)

    for did in buckets["document"] + buckets["other"]:
        # 负载最低，平局取更靠后
        candidates = non_process or list(topo_order)
        best = min(
            candidates,
            key=lambda k: (load.get(k, 0), -topo_order.index(k)),
        )
        _place(did, best)

    for did in buckets["verification"]:
        _place(did, verify_anchor)

    for did in buckets["package"]:
        _place(did, package_anchor)

    cats_list = [
        {"id": did, "category": cat_of.get(did, "other")} for did in all_ids
    ]
    problems = validate_assignment_hard_gate(
        assignment=assignment,
        manifest=manifest,
        tasks=tasks,
        categories=cats_list,
        roles=roles,
        topo_order=topo_order,
    )
    if problems:
        # 塌缩：全部非冻结挤到 last
        frozen = set((frozen_assignment or {}).keys())
        for did in all_ids:
            if did not in frozen:
                assignment[did] = last
        problems = validate_assignment_hard_gate(
            assignment=assignment,
            manifest=manifest,
            tasks=tasks,
            categories=cats_list,
            roles=roles,
            topo_order=topo_order,
        )
    if problems:
        raise DeliveryAssignmentError(
            "确定性兜底仍未过硬门：" + "；".join(problems[:8]),
            problems=problems,
        )
    return assignment


def topo_keys_from_deps(
    task_keys: list[str],
    deps: Iterable[tuple[str, str]],
) -> list[str]:
    """deps: (task, depends_on) — Kahn；无边则保持输入顺序。"""
    keys = list(task_keys)
    key_set = set(keys)
    indeg = {k: 0 for k in keys}
    succ: dict[str, list[str]] = {k: [] for k in keys}
    for task, dep in deps:
        if task not in key_set or dep not in key_set:
            continue
        succ[dep].append(task)
        indeg[task] += 1
    from collections import deque

    q = deque([k for k in keys if indeg[k] == 0])
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if len(order) != len(keys):
        # 环：退回原序
        return keys
    return order


async def _llm_json(llm: LlmFn, prompt: str) -> Any:
    text = await llm(prompt)
    return _parse_json_object(text)


async def annotate_roles(
    tasks: list[dict],
    *,
    llm: LlmFn | None,
) -> dict[str, dict]:
    """返回 task_key → {role, reason}。"""
    result: dict[str, dict] = {}
    if llm is None:
        for t in tasks:
            tk = str(t["task_key"])
            result[tk] = {
                "role": fallback_role(str(t.get("name") or tk)),
                "reason": "fallback",
            }
        return result

    prompt = (
        "为每个任务标注角色。role 只能是 "
        "process|implement|verify|deliver。\n"
        "输出 JSON：{\"roles\":[{\"task_key\":\"...\",\"role\":\"...\","
        "\"reason\":\"...\"}]}\n"
        f"任务：{json.dumps(tasks, ensure_ascii=False)}\n"
    )
    try:
        data = await _llm_json(llm, prompt)
        rows = data.get("roles") if isinstance(data, dict) else data
        for row in rows or []:
            tk = str(row.get("task_key") or "")
            role = str(row.get("role") or "")
            if tk and role in ROLES:
                result[tk] = {
                    "role": role,
                    "reason": str(row.get("reason") or ""),
                }
    except Exception:  # noqa: BLE001
        logger.debug("annotate_roles llm failed", exc_info=True)

    for t in tasks:
        tk = str(t["task_key"])
        if tk not in result:
            result[tk] = {
                "role": fallback_role(str(t.get("name") or tk)),
                "reason": "fallback",
            }
    return result


async def annotate_categories(
    deliverables: list[dict],
    *,
    llm: LlmFn | None,
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if llm is None:
        for d in deliverables:
            did = str(d["id"])
            result[did] = {
                "category": fallback_category(d),
                "reason": "fallback",
            }
        return result

    slim = [
        {"id": d.get("id"), "name": d.get("name"), "match": d.get("match")}
        for d in deliverables
    ]
    prompt = (
        "为每个交付清单项标注类别。category 只能是 "
        "source|verification|document|package|other。\n"
        "输出 JSON：{\"categories\":[{\"id\":\"...\",\"category\":\"...\","
        "\"reason\":\"...\"}]}\n"
        f"清单：{json.dumps(slim, ensure_ascii=False)}\n"
    )
    try:
        data = await _llm_json(llm, prompt)
        rows = data.get("categories") if isinstance(data, dict) else data
        for row in rows or []:
            did = str(row.get("id") or "")
            cat = str(row.get("category") or "")
            if did and cat in CATEGORIES:
                result[did] = {
                    "category": cat,
                    "reason": str(row.get("reason") or ""),
                }
    except Exception:  # noqa: BLE001
        logger.debug("annotate_categories llm failed", exc_info=True)

    for d in deliverables:
        did = str(d["id"])
        if did not in result:
            result[did] = {
                "category": fallback_category(d),
                "reason": "fallback",
            }
    return result


async def _assign_one_chunk(
    *,
    item: dict,
    remaining: set[str],
    task_keys: set[str],
    accepted: dict[str, str],
    load: dict[str, int],
    tasks: list[dict],
    roles: dict[str, str],
    categories: dict[str, dict],
    llm: LlmFn,
) -> list[dict]:
    """分配单个清单项；片级重试 ≤ CHUNK_RETRY_MAX。"""
    last_err = "未知"
    for _attempt in range(CHUNK_RETRY_MAX + 1):
        prompt = (
            "将下列交付项分配给恰好一个主责任务。"
            "输出 JSON：{\"assignments\":[{\"id\":\"...\","
            "\"task_key\":\"...\",\"reason\":\"...\"}]}\n"
            f"待分配：{json.dumps(item, ensure_ascii=False)}\n"
            f"剩余 id：{sorted(remaining)}\n"
            f"已分配：{json.dumps(accepted, ensure_ascii=False)}\n"
            f"节点负载：{json.dumps(load, ensure_ascii=False)}\n"
            f"任务：{json.dumps(tasks, ensure_ascii=False)}\n"
            f"角色：{json.dumps(roles, ensure_ascii=False)}\n"
            f"类别：{json.dumps({k: v.get('category') for k, v in categories.items()}, ensure_ascii=False)}\n"  # noqa: E501
            "规则：source 勿挂 process/deliver；"
            "verification 勿早于 implement；单一主责。\n"
        )
        try:
            data = await _llm_json(llm, prompt)
            rows = data.get("assignments") if isinstance(data, dict) else data
            chunk = list(rows or [])
            ok, err = validate_chunk_assignment(
                chunk=chunk, remaining=remaining, task_keys=task_keys,
            )
            if ok:
                return chunk
            last_err = err
        except DeliveryAssignmentError as e:
            last_err = e.message
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:200]
    raise DeliveryAssignmentError(f"片分配失败：{last_err}")


async def review_assignment(
    *,
    assignment: dict[str, str],
    roles: dict[str, str],
    categories: dict[str, dict],
    load: dict[str, int],
    llm: LlmFn,
) -> dict:
    prompt = (
        "复核交付认领表。只输出 JSON："
        "{\"ok\":true|false,\"missing_ids\":[],\"bad_pairs\":[],\"notes\":\"\"}\n"
        f"分配：{json.dumps(assignment, ensure_ascii=False)}\n"
        f"角色：{json.dumps(roles, ensure_ascii=False)}\n"
        f"类别：{json.dumps({k: v.get('category') for k, v in categories.items()}, ensure_ascii=False)}\n"  # noqa: E501
        f"负载：{json.dumps(load, ensure_ascii=False)}\n"
    )
    data = await _llm_json(llm, prompt)
    if not isinstance(data, dict):
        return {"ok": True, "missing_ids": [], "bad_pairs": [], "notes": ""}
    return data


async def run_assignment(
    *,
    manifest: dict,
    tasks: list[dict],
    topo_order: list[str],
    llm: LlmFn | None,
    frozen_assignment: dict[str, str] | None = None,
) -> tuple[dict[str, str], str]:
    """主路径：标注 → 分次分配 → 硬门 → 可选复核；失败则确定性兜底。

    返回 (id→task_key, assignment_mode) — mode 为 llm | deterministic_fallback。
    frozen_assignment: ACCEPTED 认领冻结（重规划复检）。
    """
    deliverables = list(manifest.get("deliverables") or [])
    if not deliverables:
        return dict(frozen_assignment or {}), "llm"

    task_keys = {str(t["task_key"]) for t in tasks}
    if not task_keys or not topo_order:
        raise DeliveryAssignmentError(
            "无可分配任务",
            problems=["no_tasks"],
        )

    role_ann = await annotate_roles(tasks, llm=llm)
    roles = {k: v["role"] for k, v in role_ann.items()}
    cat_ann = await annotate_categories(deliverables, llm=llm)
    cat_ann = coerce_package_marked_source(deliverables, cat_ann)

    accepted: dict[str, str] = dict(frozen_assignment or {})
    for did, tk in list(accepted.items()):
        if tk not in task_keys:
            del accepted[did]

    all_ids = [str(d["id"]) for d in deliverables]
    remaining = {i for i in all_ids if i not in accepted}
    load: dict[str, int] = {k: 0 for k in task_keys}
    for tk in accepted.values():
        if tk in load:
            load[tk] += 1

    cats_list = [
        {"id": i, "category": cat_ann[i]["category"]}
        for i in all_ids if i in cat_ann
    ]

    if llm is None:
        assignment = deterministic_assign(
            manifest=manifest,
            tasks=tasks,
            topo_order=topo_order,
            roles=roles,
            categories=cat_ann,
            frozen_assignment=accepted or None,
        )
        return assignment, "deterministic_fallback"

    async def _try_fill() -> dict[str, str]:
        nonlocal accepted, remaining, load
        if len(remaining) <= 3:
            items = [
                d for d in deliverables if str(d["id"]) in remaining
            ]
            prompt = (
                "一次性分配全部剩余交付项到主责任务。"
                "输出 JSON：{\"assignments\":[{\"id\",\"task_key\",\"reason\"}]}\n"
                f"待分配：{json.dumps(items, ensure_ascii=False)}\n"
                f"剩余：{sorted(remaining)}\n"
                f"已分配：{json.dumps(accepted, ensure_ascii=False)}\n"
                f"负载：{json.dumps(load, ensure_ascii=False)}\n"
                f"任务：{json.dumps(tasks, ensure_ascii=False)}\n"
                f"角色：{json.dumps(roles, ensure_ascii=False)}\n"
            )
            data = await _llm_json(llm, prompt)
            rows = list(
                (data.get("assignments") if isinstance(data, dict) else data)
                or []
            )
            ok, err = validate_chunk_assignment(
                chunk=rows, remaining=remaining, task_keys=task_keys,
            )
            if not ok:
                raise DeliveryAssignmentError(err)
            accepted = merge_chunk_assignments(accepted, rows)
            for row in rows:
                tk = str(row["task_key"])
                load[tk] = load.get(tk, 0) + 1
                remaining.discard(str(row["id"]))
            return accepted

        for d in deliverables:
            did = str(d["id"])
            if did not in remaining:
                continue
            chunk = await _assign_one_chunk(
                item={"id": d.get("id"), "name": d.get("name"),
                      "match": d.get("match")},
                remaining=remaining,
                task_keys=task_keys,
                accepted=accepted,
                load=load,
                tasks=tasks,
                roles=roles,
                categories=cat_ann,
                llm=llm,
            )
            accepted = merge_chunk_assignments(accepted, chunk)
            for row in chunk:
                tk = str(row["task_key"])
                load[tk] = load.get(tk, 0) + 1
                remaining.discard(str(row["id"]))
        return accepted

    last_problems: list[str] = []
    for repair in range(ASSIGN_REPAIR_MAX + 1):
        try:
            if repair > 0 and llm is not None:
                # 返工：清空非冻结部分
                accepted = dict(frozen_assignment or {})
                remaining = {i for i in all_ids if i not in accepted}
                load = {k: 0 for k in task_keys}
                for tk in accepted.values():
                    if tk in load:
                        load[tk] += 1
            await _try_fill()
        except DeliveryAssignmentError as e:
            last_problems = [e.message]
            continue

        problems = validate_assignment_hard_gate(
            assignment=accepted,
            manifest=manifest,
            tasks=tasks,
            categories=cats_list,
            roles=roles,
            topo_order=topo_order,
        )
        if problems:
            last_problems = problems
            continue

        # 硬门通过 → 可选复核（最多 1 次重派）
        if llm is not None:
            try:
                rev = await review_assignment(
                    assignment=accepted,
                    roles=roles,
                    categories=cat_ann,
                    load=load,
                    llm=llm,
                )
                if rev.get("ok") is False and repair < ASSIGN_REPAIR_MAX:
                    # 触发相关片重派：丢掉 bad/missing，再填
                    bad_ids = set(str(x) for x in (rev.get("missing_ids") or []))
                    for pair in rev.get("bad_pairs") or []:
                        if isinstance(pair, dict) and pair.get("id"):
                            bad_ids.add(str(pair["id"]))
                        elif isinstance(pair, str):
                            bad_ids.add(pair)
                    if bad_ids:
                        for did in bad_ids:
                            accepted.pop(did, None)
                        remaining = {
                            i for i in all_ids if i not in accepted
                        }
                        await _try_fill()
                        problems2 = validate_assignment_hard_gate(
                            assignment=accepted,
                            manifest=manifest,
                            tasks=tasks,
                            categories=cats_list,
                            roles=roles,
                            topo_order=topo_order,
                        )
                        if problems2:
                            last_problems = problems2
                            continue
                        return accepted, "llm"
            except Exception:  # noqa: BLE001
                logger.debug("assignment review failed", exc_info=True)
        return accepted, "llm"

    # SYS-130: LLM 耗尽 → 确定性兜底（认领必成）
    logger.warning(
        "assignment llm path failed, using deterministic fallback: %s",
        "；".join(last_problems[:6]),
    )
    assignment = deterministic_assign(
        manifest=manifest,
        tasks=tasks,
        topo_order=topo_order,
        roles=roles,
        categories=cat_ann,
        frozen_assignment=dict(frozen_assignment or {}) or None,
    )
    return assignment, "deterministic_fallback"


async def assign_in_session(
    session: Any,
    project_id: int,
    *,
    llm: LlmFn | None = None,
    freeze_accepted: bool = True,
) -> dict[str, Any]:
    """在给定 AsyncSession 上完成认领（不 commit）。供 Replanner 同库复用。"""
    from sqlalchemy import select

    from project_runtime.execution.service import (
        PLAN_STATUS_INVALID,
        PLAN_STATUS_READY,
    )
    from project_runtime.procedure.delivery_manifest import validate_manifest
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.project_state.models import (
        Project,
        Task,
        TaskDependency,
    )

    project = await session.get(Project, project_id)
    if project is None:
        raise DeliveryAssignmentError("项目不存在")
    project.plan_status = "ASSIGNING"

    if not project.delivery_manifest:
        project.plan_status = PLAN_STATUS_READY
        return {"delivery_assignment": "skipped", "plan_status": "READY"}

    manifest = validate_manifest(project.delivery_manifest)
    tasks = list(
        (
            await session.execute(
                select(Task).where(Task.project_id == project_id)
            )
        ).scalars().all()
    )
    # SYS-131: 有清单无任务 → INVALID，但不删图（无图可删）
    if not tasks:
        if manifest.get("deliverables"):
            project.plan_status = PLAN_STATUS_INVALID
            raise DeliveryAssignmentError(
                "无可分配任务但清单非空",
                problems=["no_tasks"],
            )
        project.plan_status = PLAN_STATUS_READY
        return {"delivery_assignment": "no_tasks", "plan_status": "READY"}

    deps = list(
        (
            await session.execute(
                select(TaskDependency).where(
                    TaskDependency.task_id.in_([t.id for t in tasks])
                )
            )
        ).scalars().all()
    )
    name_by_id = {t.id: t.name for t in tasks}
    task_specs = [
        {"task_key": t.name, "name": t.name, "task_type": t.task_type}
        for t in tasks
    ]
    dep_pairs = [
        (name_by_id[d.task_id], name_by_id[d.depends_on_id])
        for d in deps
        if d.task_id in name_by_id and d.depends_on_id in name_by_id
    ]
    topo = topo_keys_from_deps([t.name for t in tasks], dep_pairs)
    refs = {t.name: (t.playbook_ref or "{}") for t in tasks}
    frozen: dict[str, str] = {}
    if freeze_accepted:
        for t in tasks:
            if t.status != TaskStatus.ACCEPTED:
                continue
            try:
                pref = json.loads(t.playbook_ref or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for did in pref.get("deliverable_ids") or []:
                frozen[str(did)] = t.name

    try:
        assignment, assignment_mode = await run_assignment(
            manifest=manifest,
            tasks=task_specs,
            topo_order=topo,
            llm=llm,
            frozen_assignment=frozen or None,
        )
    except DeliveryAssignmentError:
        # SYS-129/131: 不可修复才 INVALID；禁止删任务图
        project.plan_status = PLAN_STATUS_INVALID
        raise

    new_refs = apply_assignment_to_playbook_refs(refs, assignment)
    accepted_names = {
        t.name for t in tasks if t.status == TaskStatus.ACCEPTED
    }
    for t in tasks:
        if t.name in accepted_names:
            continue
        if t.name in new_refs:
            try:
                pref = json.loads(new_refs[t.name])
            except (json.JSONDecodeError, TypeError):
                pref = {}
            if isinstance(pref, dict):
                pref["assignment_mode"] = assignment_mode
                t.playbook_ref = json.dumps(pref, ensure_ascii=False)
            else:
                t.playbook_ref = new_refs[t.name]
    await session.flush()

    # SYS-119: 认领后过载检测 / 拆分（内存图；失败不落拆分）
    from project_runtime.procedure.plan_decompose import (
        PlanDecomposeError,
        build_memory_graph_from_tasks,
        check_graph_loads,
        commit_decomposed_graph,
        decompose_memory_graph,
        seed_obligation_tokens,
    )
    from project_runtime.procedure.task_load import (
        LOAD_MAX,
        compute_task_load,
    )

    # refresh tasks after assignment write
    tasks = list(
        (
            await session.execute(
                select(Task).where(Task.project_id == project_id)
            )
        ).scalars().all()
    )
    deps = list(
        (
            await session.execute(
                select(TaskDependency).where(
                    TaskDependency.task_id.in_([t.id for t in tasks])
                )
            )
        ).scalars().all()
    )
    name_by_id = {t.id: t.name for t in tasks}
    dep_pairs = [
        (name_by_id[d.task_id], name_by_id[d.depends_on_id])
        for d in deps
        if d.task_id in name_by_id and d.depends_on_id in name_by_id
    ]
    # roles from assignment annotations stored? use fallback via build
    graph = build_memory_graph_from_tasks(tasks, dep_pairs)
    deliverables_by_id = {
        str(d["id"]): d for d in (manifest.get("deliverables") or [])
    }
    categories = {
        str(d["id"]): str(d.get("category") or "other")
        for d in (manifest.get("deliverables") or [])
    }
    seed_obligation_tokens(graph, deliverables_by_id=deliverables_by_id)
    overload = check_graph_loads(graph)
    decompose_stats: dict[str, Any] = {
        "decomposed": 0, "bin_packed": 0, "overloads": 0,
    }
    original_ids = {t.name: t.id for t in tasks}
    if overload:
        try:
            decompose_stats = await decompose_memory_graph(
                graph,
                manifest=manifest,
                categories=categories,
                llm=llm,
            )
            await commit_decomposed_graph(
                session, project_id, graph,
                original_task_ids=original_ids,
            )
        except PlanDecomposeError:
            project.plan_status = PLAN_STATUS_INVALID
            # 回写令牌到认领后、拆分前的节点（便于观测）；不落拆分图
            from project_runtime.procedure.task_load import (
                expand_obligation_tokens,
            )

            for t in tasks:
                if t.name in accepted_names:
                    continue
                try:
                    pref = json.loads(t.playbook_ref or "{}")
                except (json.JSONDecodeError, TypeError):
                    pref = {}
                if not isinstance(pref, dict):
                    pref = {}
                toks = expand_obligation_tokens(
                    deliverable_ids=list(pref.get("deliverable_ids") or []),
                    deliverables_by_id=deliverables_by_id,
                    stage_outputs=list(pref.get("stage_outputs") or []),
                )
                pref["obligation_tokens"] = toks
                pref["task_load"] = compute_task_load(toks)
                pref["load_max"] = LOAD_MAX
                t.playbook_ref = json.dumps(pref, ensure_ascii=False)
            raise
    else:
        # 无过载：仍写入令牌与 load，便于 Dashboard / Stop
        for t in tasks:
            if t.name in accepted_names:
                continue
            nd = graph["nodes"].get(t.name)
            if not nd:
                continue
            try:
                pref = json.loads(t.playbook_ref or "{}")
            except (json.JSONDecodeError, TypeError):
                pref = {}
            if not isinstance(pref, dict):
                pref = {}
            pref["obligation_tokens"] = list(nd.get("obligation_tokens") or [])
            pref["task_load"] = int(nd.get("load") or 0)
            pref["load_max"] = LOAD_MAX
            pref["assignment_role"] = nd.get("role")
            t.playbook_ref = json.dumps(pref, ensure_ascii=False)

    project.plan_status = PLAN_STATUS_READY
    return {
        "delivery_assignment": "ok",
        "plan_status": "READY",
        "assigned": len(assignment),
        "frozen": len(frozen),
        "assignment_mode": assignment_mode,
        "plan_decompose": decompose_stats,
    }


async def complete_delivery_assignment(
    project_id: int,
    *,
    llm: LlmFn | None = None,
    freeze_accepted: bool = True,
) -> dict[str, Any]:
    """独立短事务编排：读图 →（可选）LLM → 写 ids / plan_status。

    供 /plan 使用（专用 engine session）。失败抛 DeliveryAssignmentError。
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.database import engine
    from project_runtime.procedure.plan_decompose import PlanDecomposeError

    async with AsyncSession(engine) as session:
        try:
            result = await assign_in_session(
                session,
                project_id,
                llm=llm,
                freeze_accepted=freeze_accepted,
            )
            await session.commit()
            return result
        except (DeliveryAssignmentError, PlanDecomposeError):
            await session.commit()
            raise


async def default_llm_fn(prompt: str) -> str:
    from project_runtime.llm import (
        load_llm_settings,
        normalize_model_for_gateway,
    )
    from project_runtime.llm.provider import llm_acomplete

    cfg = load_llm_settings()
    if not (cfg.base_url and cfg.api_key and cfg.model):
        raise DeliveryAssignmentError("LLM 未配置，无法分配交付清单")
    result = await llm_acomplete(
        model=normalize_model_for_gateway(cfg.model, cfg.base_url),
        api_base=cfg.base_url,
        api_key=cfg.api_key,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        timeout=120,
    )
    if not result.get("ok"):
        raise DeliveryAssignmentError(
            result.get("error") or "LLM 调用失败"
        )
    try:
        return result["response"].choices[0].message.content or ""
    except (AttributeError, IndexError) as e:
        raise DeliveryAssignmentError("LLM 无内容") from e
