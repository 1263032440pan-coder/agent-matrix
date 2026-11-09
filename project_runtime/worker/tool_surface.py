"""工具面构建器（方案 §13.3）— 白名单 ∩ enabled ∩ usable ∩ 有工具卡。"""

from __future__ import annotations

import logging
from typing import Any

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry
from project_runtime.worker.tool_card import (
    DEFAULT_DESC_BUDGET,
    GATEWAY_DESC_CAP,
    resolve_tool_card,
    schema_from_tool_card,
)

logger = logging.getLogger(__name__)

_FILE_TOOL_NAMES = frozenset({
    "tool:read_file", "tool:list_dir", "tool:write_file", "tool:append_file",
    "read_file", "list_dir", "write_file", "append_file",
})

# Q6：工具手册总预算
DEFAULT_MANUAL_BUDGET = 1500
TOOL_MANUAL_MARKER = "【工具手册】"


def _bare(name: str) -> str:
    return name.split(":", 1)[-1]


def build_obligation_manual(
    cards_by_bare: dict[str, dict],
    obligation: set[str],
    *,
    budget: int = DEFAULT_MANUAL_BUDGET,
) -> tuple[str, list[dict]]:
    """事前注入：义务相关工具的 detail 概要 + 最小示例。

    返回 (手册文本, snapshot.manual 条目列表)。
    """
    if not obligation:
        return "", []
    parts: list[str] = []
    entries: list[dict] = []
    used = 0
    for bare in sorted(obligation):
        card = cards_by_bare.get(bare) or cards_by_bare.get(f"tool:{bare}")
        if not card:
            continue
        detail = card.get("detail") or {}
        if isinstance(detail, str) and detail.strip():
            body = detail.strip()
        elif isinstance(detail, dict) and detail:
            # 优先 overview / summary 小节，否则拼前两个小节
            if "overview" in detail:
                body = str(detail["overview"])
            else:
                chunks = []
                for k, v in list(detail.items())[:2]:
                    chunks.append(f"[{k}] {v}")
                body = "\n".join(chunks)
        else:
            # 无 detail 时用 summary 一行
            body = str(card.get("summary") or "").strip()
        if not body:
            continue
        block = f"### {bare}\n{body}"
        if used + len(block) + 1 > budget:
            break
        parts.append(block)
        used += len(block) + 1
        entries.append({
            "tool": bare, "source": "OBLIGATION", "chars": len(block),
        })
    if not parts:
        return "", []
    text = TOOL_MANUAL_MARKER + "\n" + "\n\n".join(parts)
    if len(text) > budget:
        text = text[:budget]
    return text, entries


def attach_error_detail(
    card: dict | None,
    error_code: str | None,
    *,
    prior_count: int,
    seen_keys: set[str],
) -> str | None:
    """出错补充说明。prior_count: 同工具同错误码已出现次数（含本次前）。

    第 1 次：相关小节；第 2 次：完整 detail；第 3 次起：None。
    """
    if not card or not error_code:
        return None
    bare_key = f"{card.get('name') or ''}:{error_code}"
    if prior_count >= 2:
        return None
    detail = card.get("detail") or {}
    if not isinstance(detail, dict):
        detail = {"overview": str(detail)} if detail else {}
    mapping = card.get("error_sections") or {}
    section_key = mapping.get(error_code) or mapping.get(
        str(error_code).split(".")[0]
    )
    if prior_count == 0:
        # 第一次：小节或 overview
        if section_key and section_key in detail:
            text = str(detail[section_key])
        elif "overview" in detail:
            text = str(detail["overview"])
        elif detail:
            text = str(next(iter(detail.values())))
        else:
            return None
        key = f"{bare_key}:{section_key or 'overview'}"
        if key in seen_keys:
            return None
        seen_keys.add(key)
        return text
    # 第二次：完整 detail
    if not detail:
        return None
    full_key = f"{bare_key}:full"
    if full_key in seen_keys:
        return None
    seen_keys.add(full_key)
    return "\n".join(f"[{k}] {v}" for k, v in detail.items())


def provider_is_usable(provider: Any, registry: ExtensionRegistry) -> bool:
    """同步可用性：优先 ``is_usable(registry)``；无则视为可用。

    不在此调用 async ``health()``（避免事件循环已在跑时产生未 await 协程）。
    缺依赖类工具应实现同步 ``is_usable``（如 office_*）。
    """
    fn = getattr(provider, "is_usable", None)
    if callable(fn):
        try:
            return bool(fn(registry))
        except TypeError:
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001
                logger.debug(
                    "is_usable failed for %s",
                    getattr(provider, "name", "?"),
                    exc_info=True,
                )
                return False
    return True


def _sort_key(
    name: str,
    priority: int,
    obligation: set[str],
) -> tuple[int, int, str]:
    bare = _bare(name)
    if bare in obligation or name in obligation:
        group = 0
    elif name in _FILE_TOOL_NAMES or bare in _FILE_TOOL_NAMES:
        group = 1
    else:
        group = 2
    return (group, -priority, name)


def build_tool_surface(
    registry: ExtensionRegistry,
    *,
    whitelist: list[str] | None = None,
    obligation_plugins: set[str] | frozenset[str] | None = None,
    desc_budget: int = DEFAULT_DESC_BUDGET,
) -> dict[str, Any]:
    """构建模型 tools= 列表 + 快照（手册注入见 T3，本函数预留 manual 字段）。"""
    from project_runtime.worker.tools import expand_tool_whitelist

    allowed = (
        None if whitelist is None
        else set(expand_tool_whitelist(whitelist) or [])
    )
    obligation = set(obligation_plugins or ())
    filtered: list[dict] = []
    candidates: list[tuple[Any, Any, dict]] = []  # info, provider, card

    for info in registry.list_providers(CapabilityType.TOOL):
        name = info.name
        bare = _bare(name)
        if allowed is not None and bare not in allowed and name not in allowed:
            filtered.append({"name": name, "reason": "NOT_WHITELISTED"})
            continue
        if not info.enabled:
            filtered.append({"name": name, "reason": "DISABLED"})
            continue
        provider = info.provider
        if provider is None:
            filtered.append({"name": name, "reason": "NO_PROVIDER"})
            continue
        if not provider_is_usable(provider, registry):
            filtered.append({"name": name, "reason": "UNHEALTHY"})
            continue
        card = resolve_tool_card(provider)
        if card is None:
            filtered.append({"name": name, "reason": "NO_CARD"})
            continue
        candidates.append((info, provider, card))

    candidates.sort(
        key=lambda row: _sort_key(row[0].name, row[0].priority, obligation)
    )

    tools: list[dict] = []
    visible: list[str] = []
    cards_by_bare: dict[str, dict] = {}
    for info, provider, card in candidates:
        llm_name = getattr(provider, "llm_name", None)
        schema = schema_from_tool_card(
            info.name, card, budget=desc_budget, llm_name=llm_name,
        )
        # 网关保险截断
        desc = schema.get("description") or schema["name"]
        if len(desc) > GATEWAY_DESC_CAP:
            desc = desc[:GATEWAY_DESC_CAP]
        tools.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": desc,
                "parameters": schema.get("parameters") or {
                    "type": "object", "properties": {},
                },
            },
        })
        visible.append(schema["name"])
        bare = _bare(info.name)
        card_named = {**card, "name": info.name}
        cards_by_bare[bare] = card_named
        cards_by_bare[info.name] = card_named
        if llm_name:
            cards_by_bare[_bare(str(llm_name))] = card_named

    manual, manual_entries = build_obligation_manual(
        cards_by_bare, obligation,
    )
    snapshot = {
        "visible": visible,
        "filtered": filtered,
        "ordering_basis": sorted(obligation),
        "manual": manual_entries,
        "degraded": [],
    }
    return {
        "tools": tools if tools else ([] if allowed is not None else None),
        "manual": manual,
        "snapshot": snapshot,
    }
