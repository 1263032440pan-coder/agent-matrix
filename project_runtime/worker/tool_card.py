"""工具卡合同（方案 §13.2）— 描述随工具模块走。

字段：
  summary / when_not / when → 进 tools=（按块拼装）
  parameters → JSON Schema
  detail → 不进 tools=（义务/出错注入）
  error_sections → 错误码 → detail 小节
  needs_workspace_root → Worker 注入声明
"""

from __future__ import annotations

from typing import Any

# Q6 建议：单工具 ≤400 字；600 作网关保险
DEFAULT_DESC_BUDGET = 400
GATEWAY_DESC_CAP = 600


def tool_card_from_legacy(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """兼容：旧 TOOL_SCHEMAS 整段 description → summary。"""
    return {
        "name": name,
        "summary": str(schema.get("description") or name).strip(),
        "when_not": "",
        "when": "",
        "parameters": schema.get("parameters")
        or {"type": "object", "properties": {}},
        "detail": {},
        "error_sections": {},
        "needs_workspace_root": False,
    }


def compose_tool_description(
    card: dict[str, Any],
    *,
    budget: int = DEFAULT_DESC_BUDGET,
) -> str:
    """按块拼装：summary → when_not → when；超预算先舍 when，再舍 when_not。"""
    summary = str(card.get("summary") or "").strip()
    when_not = str(card.get("when_not") or "").strip()
    when = str(card.get("when") or "").strip()

    blocks = [b for b in (summary, when_not, when) if b]
    if not blocks:
        return ""

    def _join(parts: list[str]) -> str:
        return "\n".join(parts)

    # 全量能放下
    full = _join(blocks)
    if len(full) <= budget:
        return full

    # 先去掉 when
    without_when = [b for b in (summary, when_not) if b]
    text = _join(without_when)
    if len(text) <= budget:
        return text

    # 再去掉 when_not，只留 summary（必要时硬截，但整块优先）
    if summary:
        return summary if len(summary) <= budget else summary[:budget]
    if when_not:
        return when_not if len(when_not) <= budget else when_not[:budget]
    return when[:budget] if when else ""


def schema_from_tool_card(
    name: str,
    card: dict[str, Any],
    *,
    budget: int = DEFAULT_DESC_BUDGET,
    llm_name: str | None = None,
) -> dict[str, Any]:
    """工具卡 → OpenAI/LiteLLM function schema。"""
    desc = compose_tool_description(card, budget=budget)
    if len(desc) > GATEWAY_DESC_CAP:
        desc = desc[:GATEWAY_DESC_CAP]
    return {
        "name": llm_name or name,
        "description": desc or name,
        "parameters": card.get("parameters")
        or {"type": "object", "properties": {}},
    }


def normalize_detail(detail: Any) -> dict[str, str]:
    """detail 统一为 {section: text}；字符串 → overview。"""
    if not detail:
        return {}
    if isinstance(detail, str):
        return {"overview": detail.strip()} if detail.strip() else {}
    if isinstance(detail, dict):
        return {str(k): str(v) for k, v in detail.items() if str(v).strip()}
    return {"overview": str(detail)}


def list_detail_sections(card: dict[str, Any] | None) -> list[str]:
    """T4 预留：返回 detail 小节目录（不上线 tool_help）。"""
    if not card:
        return []
    return list(normalize_detail(card.get("detail")).keys())


def read_detail_section(
    card: dict[str, Any] | None,
    section: str | None = None,
) -> dict[str, Any]:
    """T4 预留：读 detail 小节。

    - section 为空：只返回目录（约百字内），不吐正文
    - section 有值：返回该小节正文；未知小节 → ok=False
    第一版不注册为 TOOL；供未来 tool_help / 单测调用。
    """
    sections = normalize_detail((card or {}).get("detail"))
    if not section:
        names = list(sections.keys())
        toc = "可用小节: " + (", ".join(names) if names else "（无）")
        return {
            "ok": True,
            "mode": "toc",
            "sections": names,
            "text": toc[:120],
        }
    if section not in sections:
        return {
            "ok": False,
            "error": f"未知小节: {section}",
            "sections": list(sections.keys()),
        }
    return {
        "ok": True,
        "mode": "section",
        "section": section,
        "text": sections[section],
    }


def resolve_tool_card(provider: Any) -> dict[str, Any] | None:
    """从 Provider 取工具卡；无则从 tool_schema / 旧表兼容。"""
    card_fn = getattr(provider, "tool_card", None)
    if callable(card_fn):
        card = card_fn()
        if isinstance(card, dict) and (card.get("summary") or card.get("parameters")):
            return card

    schema_fn = getattr(provider, "tool_schema", None)
    if not callable(schema_fn):
        return None
    try:
        schema = schema_fn()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(schema, dict) or not schema.get("name"):
        return None
    # 已是完整 schema：抽成卡
    return tool_card_from_legacy(
        str(schema["name"]),
        {
            "description": schema.get("description") or schema["name"],
            "parameters": schema.get("parameters"),
        },
    )
