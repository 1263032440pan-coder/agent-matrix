"""LLM gateway compat profile — fingerprint + validity for tool-name styles."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from project_runtime.llm import LLMSettings

ToolNameStyle = Literal["colon", "underscore", "dot", "none"]

_INVALID_NAME_RE = re.compile(
    r"invalid name|only a-z|function.*name|tool.?name|validation:\s*function",
    re.IGNORECASE,
)


class GatewayCompat(BaseModel):
    probed_at: str = ""
    fingerprint: str = ""
    ok: bool = False
    chat_ok: bool = False
    tools_supported: bool = False
    tool_name_style: ToolNameStyle = "none"
    notes: str = ""


class CompatError(RuntimeError):
    """compat 缺失/过期/不支持 tools — 调用方应提示用户去设置重新测试。"""


def settings_fingerprint(base_url: str, model: str, api_key: str) -> str:
    key_hash = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()
    raw = f"{base_url or ''}\n{model or ''}\n{key_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def compat_valid(cfg: "LLMSettings") -> bool:
    c = cfg.compat
    if c is None or not c.ok or not c.fingerprint:
        return False
    return c.fingerprint == settings_fingerprint(
        cfg.base_url, cfg.model, cfg.api_key
    )


def require_tools_compat(cfg: "LLMSettings") -> GatewayCompat:
    """Agent 工具循环入口：必须有效 compat 且 tools_supported。"""
    if not compat_valid(cfg) or cfg.compat is None:
        raise CompatError(
            "网关格式未探测或已过期，请到设置重新测试连通"
        )
    c = cfg.compat
    if not c.tools_supported or c.tool_name_style == "none":
        raise CompatError(
            "当前网关不支持工具调用（设置测试已记录）。"
            "请更换支持 function calling 的网关/模型后重新测试"
        )
    return c


def is_invalid_function_name_error(exc: BaseException | str) -> bool:
    text = str(exc)
    return bool(_INVALID_NAME_RE.search(text))


def invalidate_compat(cfg: "LLMSettings") -> "LLMSettings":
    """凭据变更后作废 compat（保留字段便于 UI 展示过期）。"""
    if cfg.compat is None:
        return cfg
    cfg.compat = GatewayCompat(
        probed_at=cfg.compat.probed_at,
        fingerprint=cfg.compat.fingerprint,
        ok=False,
        chat_ok=cfg.compat.chat_ok,
        tools_supported=False,
        tool_name_style="none",
        notes="credentials changed — re-test required",
    )
    return cfg


def build_compat(
    *,
    cfg: "LLMSettings",
    chat_ok: bool,
    tools_supported: bool,
    tool_name_style: ToolNameStyle,
    notes: str,
) -> GatewayCompat:
    ok = bool(chat_ok)  # chat 通即可写 ok；tools 另字段
    # Agent 完整可用需 tools；ok 表示「探测完成且聊天可用」
    return GatewayCompat(
        probed_at=utcnow_iso(),
        fingerprint=settings_fingerprint(cfg.base_url, cfg.model, cfg.api_key),
        ok=ok,
        chat_ok=chat_ok,
        tools_supported=tools_supported,
        tool_name_style=tool_name_style if tools_supported else "none",
        notes=notes[:500],
    )


def compat_public_dict(cfg: "LLMSettings") -> dict:
    c = cfg.compat
    valid = compat_valid(cfg)
    if c is None:
        return {
            "compat_valid": False,
            "compat": None,
        }
    return {
        "compat_valid": valid,
        "compat": {
            "ok": c.ok and valid,
            "chat_ok": c.chat_ok,
            "tools_supported": c.tools_supported if valid else False,
            "tool_name_style": c.tool_name_style if valid else "none",
            "probed_at": c.probed_at,
            "notes": c.notes,
        },
    }
