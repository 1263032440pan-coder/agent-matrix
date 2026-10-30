"""计划期过载拆分（SYS-119～126）——逐节点 LLM + 片级硬门 + 确定性装箱。"""

# G/T/Q 对齐方案符号（内存图 / 当前节点 / 过载队列）
# ruff: noqa: N803, N806

from __future__ import annotations

import copy
import json
import logging
import re
from collections import deque
from typing import Any, Awaitable, Callable

from project_runtime.procedure.delivery_assignment import (
    ROLES,
    topo_keys_from_deps,
    validate_assignment_hard_gate,
)
from project_runtime.procedure.task_load import (
    BIN_CAPACITY,
    LOAD_MAX,
    compute_task_load,
    expand_obligation_tokens,
)

logger = logging.getLogger(__name__)

DECOMPOSE_NODE_RETRY = 2
DECOMPOSE_SPLIT_BUDGET = 16
ERROR_CODE = "PLAN_DECOMPOSE_INVALID"

LlmFn = Callable[[str], Awaitable[str]]

# re-export for tests
__all__ = [
    "BIN_CAPACITY",
    "DECOMPOSE_NODE_RETRY",
    "DECOMPOSE_SPLIT_BUDGET",
    "LOAD_MAX",
    "PlanDecomposeError",
    "apply_replace_in_graph",
    "bin_pack_tokens",
    "check_graph_loads",
    "decompose_memory_graph",
    "deterministic_subs",
    "seed_obligation_tokens",
    "validate_shard",
]


class PlanDecomposeError(ValueError):
    def __init__(self, message: str, *, problems: list[str] | None = None):
        super().__init__(message)
        self.error_code = ERROR_CODE
        self.message = message
        self.problems = list(problems or [])


def _token_id(t: dict | str) -> str:
    if isinstance(t, str):
        return t
    return str(t.get("id") or "")


def _normalize_token_list(
    items: list[Any],
    catalog: dict[str, dict],
) -> list[dict]:
    out: list[dict] = []
    for it in items or []:
        tid = _token_id(it)
        if tid in catalog:
            out.append(dict(catalog[tid]))
        elif isinstance(it, dict) and it.get("id"):
            out.append(dict(it))
        else:
            out.append({"id": tid, "weight": 1})
    return out


def bin_pack_tokens(tokens: list[dict]) -> list[list[dict]]:
    """按权重装入容量 BIN_CAPACITY 的箱子；同 deliverable_id 优先同箱。"""
    # 先按 deliverable_id 聚成小组（无 id 的各成一组），组内按权重降序
    groups: dict[str, list[dict]] = {}
    solo: list[dict] = []
    for t in list(tokens or []):
        did = str(t.get("deliverable_id") or "").strip()
        if did:
            groups.setdefault(did, []).append(t)
        else:
            solo.append(t)
    for g in groups.values():
        g.sort(key=lambda t: (-int(t.get("weight") or 1), _token_id(t)))
    solo.sort(key=lambda t: (-int(t.get("weight") or 1), _token_id(t)))

    # 组按总重降序，再拼成放置序列：整组尽量连续放入
    group_list = sorted(
        groups.items(),
        key=lambda kv: (
            -sum(int(t.get("weight") or 1) for t in kv[1]),
            kv[0],
        ),
    )
    ordered: list[dict] = []
    for _did, g in group_list:
        ordered.extend(g)
    ordered.extend(solo)

    bins: list[list[dict]] = []
    loads: list[int] = []
    for t in ordered:
        w = int(t.get("weight") or 1)
        placed = False
        # 优先：已含同 deliverable_id 且放得下的箱子
        did = str(t.get("deliverable_id") or "").strip()
        if did:
            for i, load in enumerate(loads):
                if load + w > BIN_CAPACITY:
                    continue
                if any(
                    str(x.get("deliverable_id") or "").strip() == did
                    for x in bins[i]
                ):
                    bins[i].append(t)
                    loads[i] += w
                    placed = True
                    break
        if not placed:
            for i, load in enumerate(loads):
                if load + w <= BIN_CAPACITY:
                    bins[i].append(t)
                    loads[i] += w
                    placed = True
                    break
        if not placed:
            bins.append([t])
            loads.append(w)
    return bins


def deterministic_subs(node: dict) -> list[dict]:
    """确定性装箱成链；deliverable_ids 全放最后一箱。"""
    tokens = list(node.get("obligation_tokens") or [])
    bins = bin_pack_tokens(tokens)
    if len(bins) < 2:
        # 过载却只有一箱 → 发布约束破坏；强行切成至少 2 份（按枚数对半）
        if len(tokens) < 2:
            raise PlanDecomposeError(
                "单节点令牌无法拆分（单枚权重破坏发布约束）",
                problems=["token weight violates LOAD_MAX"],
            )
        mid = max(1, len(tokens) // 2)
        bins = [tokens[:mid], tokens[mid:]]
    base = str(node.get("task_key") or "task")
    role = str(node.get("role") or "implement")
    subs: list[dict] = []
    for i, b in enumerate(bins):
        is_tail = i == len(bins) - 1
        key = f"{base}-分片{i + 1}" if not is_tail else f"{base}-收口"
        # 避免与 base 完全相同
        if key == base:
            key = f"{base}-子{i + 1}"
        subs.append({
            "task_key": key,
            "role": role,
            "task_type": node.get("task_type"),
            "code_stage": bool(node.get("code_stage")),
            "deliverable_ids": (
                list(node.get("deliverable_ids") or []) if is_tail else []
            ),
            "obligation_tokens": [dict(t) for t in b],
            "contract": (
                f"完成本节点义务令牌：{[ _token_id(t) for t in b ]}"
            ),
            "bin_packed": True,
            "decomposed_from": base,
        })
    return subs


def _multiset(ids: list[str]) -> dict[str, int]:
    m: dict[str, int] = {}
    for i in ids:
        m[i] = m.get(i, 0) + 1
    return m


def _has_cycle(keys: list[str], deps: list[tuple[str, str]]) -> bool:
    key_set = set(keys)
    indeg = {k: 0 for k in keys}
    succ: dict[str, list[str]] = {k: [] for k in keys}
    for task, dep in deps:
        if task not in key_set or dep not in key_set:
            continue
        succ[dep].append(task)
        indeg[task] += 1
    q = deque([k for k in keys if indeg[k] == 0])
    seen = 0
    while q:
        n = q.popleft()
        seen += 1
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    return seen != len(keys)


def validate_shard(
    *,
    replace: str,
    T: dict,
    subs: list[dict],
    G: dict,
    categories: dict[str, str],
) -> list[str]:
    """片级硬门；空列表 = 通过。不过不改 G。"""
    problems: list[str] = []
    if replace != str(T.get("task_key") or ""):
        problems.append(f"replace 不等于本轮节点：{replace}")
    if not isinstance(subs, list) or len(subs) < 2:
        problems.append("subs 须 ≥ 2")
        return problems

    existing = set(G.get("nodes") or {}) - {replace}
    sub_keys: list[str] = []
    for s in subs:
        if not isinstance(s, dict):
            problems.append("sub 非对象")
            continue
        tk = str(s.get("task_key") or "").strip()
        if not tk:
            problems.append("sub 缺 task_key")
            continue
        if tk in existing or tk in sub_keys:
            problems.append(f"task_key 冲突：{tk}")
        role = str(s.get("role") or "")
        if role not in ROLES:
            problems.append(f"非法 role：{role}")
        sub_keys.append(tk)

    # deliverable ids partition
    orig_ids = [str(x) for x in (T.get("deliverable_ids") or [])]
    got_ids: list[str] = []
    for s in subs:
        for did in s.get("deliverable_ids") or []:
            got_ids.append(str(did))
    if _multiset(got_ids) != _multiset(orig_ids):
        problems.append(
            f"清单 id 划分不守恒：期望 {sorted(orig_ids)} 实际 {sorted(got_ids)}"
        )
    unknown = set(got_ids) - set(orig_ids)
    if unknown:
        problems.append(f"出现原节点没有的 id：{sorted(unknown)}")

    # token multiset
    catalog = {
        _token_id(t): t for t in (T.get("obligation_tokens") or [])
    }
    orig_tids = [_token_id(t) for t in (T.get("obligation_tokens") or [])]
    got_tids: list[str] = []
    for s in subs:
        raw = s.get("obligation_tokens") or []
        # allow id strings from LLM
        for it in raw:
            tid = _token_id(it)
            got_tids.append(tid)
    if _multiset(got_tids) != _multiset(orig_tids):
        problems.append(
            f"令牌多重集合不守恒：期望 {sorted(orig_tids)} 实际 {sorted(got_tids)}"
        )

    for s in subs:
        toks = _normalize_token_list(
            list(s.get("obligation_tokens") or []), catalog,
        )
        s["_norm_tokens"] = toks
        load = compute_task_load(toks)
        if load >= LOAD_MAX:
            problems.append(
                f"子节点 {s.get('task_key')} load={load} ≥ {LOAD_MAX}"
            )
        ids = list(s.get("deliverable_ids") or [])
        if not toks and not ids:
            problems.append(
                f"子节点 {s.get('task_key')} 空令牌且无主责 id"
            )

    # id owner must be after all tokens of that id
    for did in orig_ids:
        owner_idx = None
        token_idxs: list[int] = []
        for i, s in enumerate(subs):
            if did in [str(x) for x in (s.get("deliverable_ids") or [])]:
                owner_idx = i
            for it in s.get("obligation_tokens") or []:
                tid = _token_id(it)
                # ext:/prefix:/name:/count: 带 deliverable_id
                if f":{did}:" in f":{tid}:" or tid.endswith(f":{did}") \
                        or f":{did}:" in tid:
                    token_idxs.append(i)
                    break
            # also match tokens whose id embeds deliverable
            for it in s.get("obligation_tokens") or []:
                tid = _token_id(it)
                parts = tid.split(":")
                if len(parts) >= 2 and parts[1] == did:
                    if i not in token_idxs:
                        token_idxs.append(i)
        if owner_idx is not None and token_idxs:
            if owner_idx < max(token_idxs):
                problems.append(
                    f"主责 {did} 须位于其令牌节点之后（链下标）"
                )

    # source → implement
    for s in subs:
        for did in s.get("deliverable_ids") or []:
            if categories.get(str(did)) == "source":
                if str(s.get("role") or "") != "implement":
                    problems.append(
                        f"source 项 {did} 主责 role 须为 implement"
                    )

    # preview wiring: no cycle + SYS-110 order on preview graph
    preview = copy.deepcopy(G)
    try:
        # normalize tokens on copy of subs for apply
        norm_subs = []
        for s in subs:
            ns = dict(s)
            ns["obligation_tokens"] = s.get("_norm_tokens") or _normalize_token_list(
                list(s.get("obligation_tokens") or []), catalog,
            )
            norm_subs.append(ns)
        if not problems:  # only wire if basic ok
            apply_replace_in_graph(preview, replace, norm_subs)
            keys = list(preview["nodes"].keys())
            deps = list(preview["deps"])
            if _has_cycle(keys, deps):
                problems.append("接线后成环")
            else:
                # SYS-109/110 via validate_assignment_hard_gate
                assignment: dict[str, str] = {}
                roles: dict[str, str] = {}
                for nk, nd in preview["nodes"].items():
                    roles[nk] = str(nd.get("role") or "implement")
                    for did in nd.get("deliverable_ids") or []:
                        assignment[str(did)] = nk
                # build fake manifest ids from assignment + categories
                manifest = {
                    "deliverables": [
                        {"id": did} for did in assignment
                    ],
                }
                # include all category ids that were on T
                for did in orig_ids:
                    if did not in assignment:
                        pass
                cats = [
                    {"id": did, "category": categories.get(did, "other")}
                    for did in assignment
                ]
                topo = topo_keys_from_deps(keys, deps)
                tasks = [{"task_key": k} for k in keys]
                gate = validate_assignment_hard_gate(
                    assignment=assignment,
                    manifest=manifest,
                    tasks=tasks,
                    categories=cats,
                    roles=roles,
                    topo_order=topo,
                )
                # Only enforce order-ish problems here for shard; full gate later
                for g in gate:
                    if g.startswith("顺序") or g.startswith("一致性"):
                        problems.append(g)
    except PlanDecomposeError as e:
        problems.append(e.message)

    return problems


def apply_replace_in_graph(
    G: dict,
    replace: str,
    subs: list[dict],
) -> None:
    """用链替换节点并接线（原地改 G）。"""
    nodes: dict = G["nodes"]
    if replace not in nodes:
        raise PlanDecomposeError(f"替换目标不存在：{replace}")
    old = nodes[replace]
    # prepare sub nodes
    new_keys: list[str] = []
    for s in subs:
        tk = str(s["task_key"])
        stage_outs = []
        for t in s.get("obligation_tokens") or []:
            tid = _token_id(t)
            if tid.startswith("output:") and t.get("label"):
                stage_outs.append(str(t["label"]))
        nodes[tk] = {
            "task_key": tk,
            "role": str(s.get("role") or old.get("role") or "implement"),
            "task_type": s.get("task_type") or old.get("task_type"),
            "code_stage": (
                bool(s["code_stage"]) if "code_stage" in s
                else bool(old.get("code_stage"))
            ),
            "deliverable_ids": [str(x) for x in (s.get("deliverable_ids") or [])],
            "obligation_tokens": [dict(t) if isinstance(t, dict) else {"id": t}
                                  for t in (s.get("obligation_tokens") or [])],
            "stage_outputs": stage_outs or list(old.get("stage_outputs") or []),
            "contract": s.get("contract") or old.get("contract"),
            "status": "CREATED",
            "frozen": False,
            "bin_packed": bool(s.get("bin_packed")),
            "decomposed_from": replace,
            "db_id": None,
        }
        new_keys.append(tk)

    deps: list[tuple[str, str]] = list(G.get("deps") or [])
    preds = [dep for task, dep in deps if task == replace]
    succs = [task for task, dep in deps if dep == replace]
    # drop edges involving replace
    deps = [
        (t, d) for t, d in deps
        if t != replace and d != replace
    ]
    # chain
    if preds:
        for p in preds:
            deps.append((new_keys[0], p))
    for i in range(1, len(new_keys)):
        deps.append((new_keys[i], new_keys[i - 1]))
    if succs:
        for s in succs:
            deps.append((s, new_keys[-1]))

    # SYS-110 修补（§5.2.6）：仅当 verification/package 主责早于
    # **新拆出的** implement 子节点时，加边「主责 → 链尾」。
    # 禁止对全图每个靠后 implement 加边（会与「返修→质量关卡」等
    # 既有边对打成环）。
    del nodes[replace]
    keys = list(nodes.keys())
    roles = {k: str(nodes[k].get("role") or "implement") for k in keys}
    topo = topo_keys_from_deps(keys, deps)
    rank = {k: i for i, k in enumerate(topo)}
    new_impl = [
        k for k in new_keys
        if roles.get(k) == "implement" and k in rank
    ]
    chain_tail = new_keys[-1]
    if new_impl and chain_tail in rank:
        min_new = min(rank[k] for k in new_impl)
        for nk in keys:
            role = roles.get(nk)
            if role not in ("verify", "deliver"):
                continue
            if nk not in rank or nk == chain_tail:
                continue
            if rank[nk] < min_new:
                edge = (nk, chain_tail)
                if edge in deps:
                    continue
                deps.append(edge)
                if _has_cycle(keys, deps):
                    deps.remove(edge)

    if _has_cycle(keys, deps):
        raise PlanDecomposeError("接线后成环", problems=["cycle"])

    G["deps"] = deps
    G.setdefault("replaced", {})[replace] = new_keys


def seed_obligation_tokens(
    G: dict,
    *,
    deliverables_by_id: dict[str, dict],
    feature_hints: list[str] | None = None,
) -> None:
    """对可拆节点展开令牌（原地）。冻结节点跳过。"""
    for _k, nd in G["nodes"].items():
        if nd.get("frozen"):
            continue
        if nd.get("obligation_tokens"):
            # already seeded
            load = compute_task_load(nd["obligation_tokens"])
            nd["load"] = load
            continue
        toks = expand_obligation_tokens(
            deliverable_ids=list(nd.get("deliverable_ids") or []),
            deliverables_by_id=deliverables_by_id,
            stage_outputs=list(nd.get("stage_outputs") or []),
            feature_hints=feature_hints if feature_hints else None,
        )
        # feature only once on whole graph would double — attach features
        # only to nodes that already have deliverable_ids or stage_outputs
        nd["obligation_tokens"] = toks
        nd["load"] = compute_task_load(toks)


def check_graph_loads(G: dict) -> list[str]:
    problems: list[str] = []
    for k, nd in G["nodes"].items():
        if nd.get("frozen"):
            continue
        load = compute_task_load(nd.get("obligation_tokens") or [])
        nd["load"] = load
        if load >= LOAD_MAX:
            problems.append(f"{k} load={load} ≥ {LOAD_MAX}")
    return problems


def overload_queue(G: dict) -> list[str]:
    keys = list(G["nodes"].keys())
    topo = topo_keys_from_deps(keys, list(G.get("deps") or []))
    out: list[str] = []
    for k in topo:
        nd = G["nodes"][k]
        if nd.get("frozen"):
            continue
        if compute_task_load(nd.get("obligation_tokens") or []) >= LOAD_MAX:
            out.append(k)
    return out


def _parse_replace_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    brace = re.search(r"\{[\s\S]*\}", raw)
    if not brace:
        raise PlanDecomposeError("模型未返回 JSON 对象")
    try:
        data = json.loads(brace.group(0))
    except json.JSONDecodeError as e:
        raise PlanDecomposeError(f"JSON 无法解析：{e}") from e
    if not isinstance(data, dict):
        raise PlanDecomposeError("JSON 根须为对象")
    return data


def build_decompose_prompt(
    *,
    T: dict,
    G: dict,
    remaining_queue: list[str],
    material_summary: str = "",
) -> str:
    tokens = T.get("obligation_tokens") or []
    token_view = [
        {
            "id": _token_id(t),
            "weight": t.get("weight", 1),
            **({"quota": t["quota"]} if "quota" in t else {}),
            **({"label": t["label"]} if t.get("label") else {}),
        }
        for t in tokens
    ]
    global_table = []
    for k, nd in G["nodes"].items():
        global_table.append({
            "task_key": k,
            "status": nd.get("status"),
            "load": nd.get("load", compute_task_load(nd.get("obligation_tokens"))),
            "role": nd.get("role"),
            "decomposed_from": nd.get("decomposed_from"),
        })
    current_blob = json.dumps(
        {
            "task_key": T.get("task_key"),
            "role": T.get("role"),
            "deliverable_ids": T.get("deliverable_ids"),
            "load": T.get("load"),
            "tokens": token_view,
            "contract": (T.get("contract") or "")[:400],
        },
        ensure_ascii=False,
    )
    return (
        "你是计划拆分助手。只拆当前过载节点，禁止修改其他节点。\n"
        f"负荷阈值 LOAD_MAX={LOAD_MAX}；每个子节点 load 必须严格 < {LOAD_MAX}。\n"
        "输出唯一 JSON：{\"replace\":\"原名\",\"subs\":[...]}。"
        "subs≥2；每项含 task_key/role/deliverable_ids/obligation_tokens/contract。\n"
        "obligation_tokens 填令牌 id 字符串列表，多重集合必须与原节点完全相同。\n"
        "清单 id 可只留在链尾；持有某 id 的子节点须排在持有该 id 令牌的子节点之后。\n"
        "source 类 id 的主责 role 必须是 implement。V0.1 子任务排成一条链。\n"
        f"【当前必拆节点】{current_blob}\n"
        f"【全局任务表·只读】{json.dumps(global_table, ensure_ascii=False)}\n"
        f"【已拆结果】{json.dumps(G.get('replaced') or {}, ensure_ascii=False)}\n"
        f"【剩余过载队列】{json.dumps(remaining_queue, ensure_ascii=False)}\n"
        f"【材料摘要·只读】{(material_summary or '')[:800]}\n"
    )


async def try_llm_split(
    *,
    T: dict,
    G: dict,
    remaining_queue: list[str],
    categories: dict[str, str],
    llm: LlmFn,
    material_summary: str = "",
) -> list[dict] | None:
    """返回通过片级校验的 subs；失败返回 None。"""
    catalog = {
        _token_id(t): t for t in (T.get("obligation_tokens") or [])
    }
    last_err = ""
    for _attempt in range(DECOMPOSE_NODE_RETRY):
        prompt = build_decompose_prompt(
            T=T, G=G, remaining_queue=remaining_queue,
            material_summary=material_summary,
        )
        if last_err:
            prompt += f"\n上次失败原因：{last_err}\n请修正后重发完整 JSON。\n"
        try:
            text = await llm(prompt)
            data = _parse_replace_json(text)
            replace = str(data.get("replace") or "")
            raw_subs = data.get("subs") or []
            if not isinstance(raw_subs, list):
                last_err = "subs 非列表"
                continue
            subs: list[dict] = []
            for s in raw_subs:
                if not isinstance(s, dict):
                    continue
                toks = _normalize_token_list(
                    list(s.get("obligation_tokens") or []), catalog,
                )
                subs.append({
                    "task_key": str(s.get("task_key") or "").strip(),
                    "role": str(s.get("role") or "implement"),
                    "deliverable_ids": [
                        str(x) for x in (s.get("deliverable_ids") or [])
                    ],
                    "obligation_tokens": toks,
                    "contract": str(s.get("contract") or ""),
                    "bin_packed": False,
                })
            problems = validate_shard(
                replace=replace, T=T, subs=subs, G=G,
                categories=categories,
            )
            if problems:
                last_err = "；".join(problems[:6])
                continue
            # finalize normalized tokens
            for s in subs:
                if "_norm_tokens" in s:
                    s["obligation_tokens"] = s.pop("_norm_tokens")
            return subs
        except PlanDecomposeError as e:
            last_err = e.message
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:200]
            logger.debug("decompose llm attempt failed", exc_info=True)
    return None


def final_graph_gate(
    G: dict,
    *,
    manifest: dict,
    categories: dict[str, str],
) -> list[str]:
    problems = check_graph_loads(G)
    keys = list(G["nodes"].keys())
    deps = list(G.get("deps") or [])
    if _has_cycle(keys, deps):
        problems.append("依赖成环")
    assignment: dict[str, str] = {}
    roles: dict[str, str] = {}
    for nk, nd in G["nodes"].items():
        roles[nk] = str(nd.get("role") or "implement")
        for did in nd.get("deliverable_ids") or []:
            did_s = str(did)
            if did_s in assignment:
                problems.append(f"双重主责：{did_s}")
            assignment[did_s] = nk
    cats = [
        {"id": str(d["id"]), "category": categories.get(str(d["id"]), "other")}
        for d in (manifest.get("deliverables") or [])
    ]
    # fill categories map
    for c in cats:
        categories.setdefault(c["id"], c["category"])
    topo = topo_keys_from_deps(keys, deps)
    tasks = [{"task_key": k} for k in keys]
    problems.extend(
        validate_assignment_hard_gate(
            assignment=assignment,
            manifest=manifest,
            tasks=tasks,
            categories=cats,
            roles=roles,
            topo_order=topo,
        )
    )
    # unchanged manifest id set
    mid = {str(d["id"]) for d in (manifest.get("deliverables") or [])}
    if set(assignment.keys()) != mid:
        problems.append(
            f"清单集合变化：期望 {sorted(mid)} 实际 {sorted(assignment)}"
        )
    return problems


async def decompose_memory_graph(
    G: dict,
    *,
    manifest: dict,
    categories: dict[str, str],
    llm: LlmFn | None = None,
    material_summary: str = "",
    feature_hints: list[str] | None = None,
) -> dict[str, Any]:
    """在内存图上完成过载拆分。返回 stats；失败抛 PlanDecomposeError。

    调用方负责：seed 后若无过载可跳过本函数。
    """
    deliverables_by_id = {
        str(d["id"]): d for d in (manifest.get("deliverables") or [])
    }
    seed_obligation_tokens(
        G, deliverables_by_id=deliverables_by_id,
        feature_hints=feature_hints,
    )
    Q = overload_queue(G)
    if not Q:
        return {"decomposed": 0, "bin_packed": 0, "overloads": 0}

    split_count = 0
    bin_packed = 0
    while Q and split_count < DECOMPOSE_SPLIT_BUDGET:
        T_key = Q.pop(0)
        if T_key not in G["nodes"]:
            continue
        T = G["nodes"][T_key]
        if T.get("frozen"):
            continue
        if compute_task_load(T.get("obligation_tokens") or []) < LOAD_MAX:
            continue
        remaining = list(Q)
        subs = None
        if llm is not None:
            subs = await try_llm_split(
                T=T, G=G, remaining_queue=remaining,
                categories=categories, llm=llm,
                material_summary=material_summary,
            )
        used_bin = False
        if subs is None:
            subs = deterministic_subs(T)
            used_bin = True
            problems = validate_shard(
                replace=T_key, T=T, subs=subs, G=G,
                categories=categories,
            )
            if problems:
                raise PlanDecomposeError(
                    "确定性装箱片级失败：" + "；".join(problems[:8]),
                    problems=problems,
                )
            for s in subs:
                if "_norm_tokens" in s:
                    s["obligation_tokens"] = s.pop("_norm_tokens")
                s["bin_packed"] = True
        apply_replace_in_graph(G, T_key, subs)
        if used_bin:
            bin_packed += 1
        split_count += 1
        # refresh loads / queue
        for nd in G["nodes"].values():
            nd["load"] = compute_task_load(nd.get("obligation_tokens") or [])
        Q = overload_queue(G)

    # budget exhausted → bin-pack remaining
    for T_key in list(overload_queue(G)):
        T = G["nodes"][T_key]
        subs = deterministic_subs(T)
        problems = validate_shard(
            replace=T_key, T=T, subs=subs, G=G, categories=categories,
        )
        if problems:
            raise PlanDecomposeError(
                "预算用尽后装箱失败：" + "；".join(problems[:8]),
                problems=problems,
            )
        for s in subs:
            if "_norm_tokens" in s:
                s["obligation_tokens"] = s.pop("_norm_tokens")
            s["bin_packed"] = True
        apply_replace_in_graph(G, T_key, subs)
        bin_packed += 1
        split_count += 1

    gate = final_graph_gate(G, manifest=manifest, categories=categories)
    if gate:
        raise PlanDecomposeError(
            "过载拆分整闸失败：" + "；".join(gate[:8]),
            problems=gate,
        )
    return {
        "decomposed": split_count,
        "bin_packed": bin_packed,
        "overloads": split_count,
        "replaced": dict(G.get("replaced") or {}),
    }


def build_memory_graph_from_tasks(
    tasks: list[Any],
    deps: list[tuple[str, str]],
    *,
    roles: dict[str, str] | None = None,
) -> dict:
    """从 ORM Task 列表构建内存图。deps: (task_key, depends_on_key)。"""
    from project_runtime.project_state.enums import TaskStatus

    nodes: dict[str, dict] = {}
    for t in tasks:
        try:
            pref = json.loads(t.playbook_ref or "{}")
        except (json.JSONDecodeError, TypeError):
            pref = {}
        if not isinstance(pref, dict):
            pref = {}
        status = t.status
        status_v = status.value if hasattr(status, "value") else str(status)
        frozen = status in (
            TaskStatus.ACCEPTED, TaskStatus.RUNNING,
        ) if hasattr(TaskStatus, "ACCEPTED") else status_v in (
            "ACCEPTED", "RUNNING",
        )
        role = (roles or {}).get(t.name) or pref.get("assignment_role")
        if not role:
            from project_runtime.procedure.delivery_assignment import (
                fallback_role,
            )
            role = fallback_role(t.name)
        nodes[t.name] = {
            "task_key": t.name,
            "role": role,
            "task_type": t.task_type,
            "code_stage": bool(pref.get("code_stage")),
            "deliverable_ids": [
                str(x) for x in (pref.get("deliverable_ids") or [])
            ],
            "obligation_tokens": list(pref.get("obligation_tokens") or []),
            "stage_outputs": list(pref.get("stage_outputs") or []),
            "contract": t.contract,
            "status": status_v,
            "frozen": frozen,
            "bin_packed": bool(pref.get("bin_packed")),
            "decomposed_from": pref.get("decomposed_from"),
            "db_id": t.id,
            "playbook_ref": pref,
        }
    return {"nodes": nodes, "deps": list(deps), "replaced": {}}


async def commit_decomposed_graph(
    session: Any,
    project_id: int,
    G: dict,
    *,
    original_task_ids: dict[str, int],
) -> None:
    """短事务写入：删除被替换节点、插入子节点、重写依赖与 playbook_ref。

    original_task_ids: 拆分前 name→id（含被替换的）。
    """
    from sqlalchemy import text as _text

    from project_runtime.project_state.enums import DependencyType
    from project_runtime.project_state.models import Task, TaskDependency
    from project_runtime.task_graph.manager import (
        CreateTaskRequest,
        TaskGraphManager,
    )

    replaced = G.get("replaced") or {}
    deleted_names = set(replaced.keys())
    # Also delete names that were intermediate and got replaced again
    surviving = set(G["nodes"].keys())
    mgr = TaskGraphManager(session)

    # delete DB tasks that are no longer in graph (and were in original)
    to_delete_ids: list[int] = []
    for name, tid in original_task_ids.items():
        if name not in surviving:
            to_delete_ids.append(tid)
    if to_delete_ids:
        idlist = ",".join(str(x) for x in to_delete_ids)
        await session.execute(
            _text(
                f'DELETE FROM "task_dependency" '
                f'WHERE task_id IN ({idlist}) '
                f'OR depends_on_id IN ({idlist})'
            )
        )
        await session.execute(
            _text(f'DELETE FROM task WHERE id IN ({idlist})')
        )
        await session.flush()

    # upsert nodes
    id_by_name: dict[str, int] = {}
    for name, nd in G["nodes"].items():
        pref = dict(nd.get("playbook_ref") or {})
        pref.update({
            "task_name": name,
            "deliverable_ids": list(nd.get("deliverable_ids") or []),
            "obligation_tokens": list(nd.get("obligation_tokens") or []),
            "stage_outputs": list(nd.get("stage_outputs") or []),
            "code_stage": bool(nd.get("code_stage")),
            "assignment_role": nd.get("role"),
            "bin_packed": bool(nd.get("bin_packed")),
            "decomposed_from": nd.get("decomposed_from"),
            "task_load": int(nd.get("load") or compute_task_load(
                nd.get("obligation_tokens") or []
            )),
            "load_max": LOAD_MAX,
        })
        ref_json = json.dumps(pref, ensure_ascii=False)
        if nd.get("db_id") and name in original_task_ids \
                and name not in deleted_names:
            task = await session.get(Task, nd["db_id"])
            if task is not None:
                task.playbook_ref = ref_json
                if nd.get("contract"):
                    task.contract = nd["contract"]
                id_by_name[name] = task.id
                continue
        # new node
        task = await mgr.create_task(CreateTaskRequest(
            project_id=project_id,
            name=name,
            task_type=str(nd.get("task_type") or "generic"),
        ))
        task.playbook_ref = ref_json
        if nd.get("contract"):
            task.contract = str(nd["contract"])
        await session.flush()
        id_by_name[name] = task.id
        nd["db_id"] = task.id

    # rewrite all deps among surviving nodes: clear then add
    surviving_ids = list(id_by_name.values())
    if surviving_ids:
        idlist = ",".join(str(x) for x in surviving_ids)
        await session.execute(
            _text(
                f'DELETE FROM "task_dependency" '
                f'WHERE task_id IN ({idlist}) '
                f'OR depends_on_id IN ({idlist})'
            )
        )
        await session.flush()
    for task_key, dep_key in G.get("deps") or []:
        tid = id_by_name.get(task_key)
        did = id_by_name.get(dep_key)
        if tid is None or did is None:
            continue
        session.add(TaskDependency(
            task_id=tid,
            depends_on_id=did,
            dependency_type=DependencyType.HARD,
        ))
    await session.flush()
