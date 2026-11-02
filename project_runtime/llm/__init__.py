"""LLM settings persistence + LiteLLM client factory.

Config is stored in data/llm_settings.json (runtime-writable), overriding
env defaults. API key is stored locally and never logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from project_runtime.llm.compat import (
    CompatError,
    GatewayCompat,
    build_compat,
    compat_public_dict,
    compat_valid,
    invalidate_compat,
    is_invalid_function_name_error,
    require_tools_compat,
    settings_fingerprint,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/llm_settings.json")


class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    compat: GatewayCompat | None = None


def llm_message_text(message: object) -> str:
    """取 assistant 可见文本；content 空时回退 reasoning_content。

    deepseek-v4-pro 等推理模型常把整段产出放在 reasoning_content，
    content 为空——直接 json.loads 会得到 char 0 空错误。
    """
    def _as_str(val: object) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts: list[str] = []
            for item in val:
                if isinstance(item, str):
                    parts.append(str(item))
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                else:
                    t = getattr(item, "text", None)
                    if t:
                        parts.append(str(t))
            return "".join(parts)
        return str(val)

    if isinstance(message, dict):
        text = _as_str(message.get("content")).strip()
        if text:
            return text
        return _as_str(message.get("reasoning_content")).strip()
    text = _as_str(getattr(message, "content", None)).strip()
    if text:
        return text
    return _as_str(getattr(message, "reasoning_content", None)).strip()


def normalize_model_for_gateway(model: str, base_url: str | None) -> str:
    """OpenAI-compatible 网关需要显式 provider 前缀。

    LiteLLM 无法从 "qwen/xxx" 之类的自定义模型名推断 provider；
    带 base_url 且模型名含 "/" 且前缀非已知 provider → 补 openai/。
    """
    if base_url and "/" in model and not model.startswith("openai/"):
        known_prefixes = ("openai/", "azure/", "anthropic/", "huggingface/",
                          "ollama/", "gemini/", "deepseek/", "mistral/")
        if not model.startswith(known_prefixes):
            return f"openai/{model}"
    return model


def load_llm_settings() -> LLMSettings:
    if _CONFIG_PATH.exists():
        try:
            return LLMSettings(**json.loads(_CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("llm_settings.json unreadable: %s", e)
    return LLMSettings()


def save_llm_settings(cfg: LLMSettings) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _probe_tool_name(
    *,
    model: str,
    api_base: str,
    api_key: str,
    function_name: str,
) -> tuple[bool, str]:
    """Return (accepted, error_snippet). Accepted = request not rejected for name."""
    import litellm

    tools = [{
        "type": "function",
        "function": {
            "name": function_name,
            "description": "connectivity probe — do not call",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    try:
        litellm.completion(
            model=model,
            api_base=api_base,
            api_key=api_key,
            messages=[{"role": "user", "content": "ping"}],
            tools=tools,
            tool_choice="none",
            max_tokens=5,
            timeout=20,
        )
        return True, ""
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        if is_invalid_function_name_error(err):
            return False, err
        # Other errors (rate limit, etc.) — treat as "name may be ok but call failed"
        # Prefer not to mark style as accepted if HTTP-level failure isn't name-related
        # and isn't a clear success. For probes, non-name errors after accept path:
        # if gateway parsed tools, name is fine.
        if "tool" in err.lower() and "support" in err.lower():
            return False, err
        # Ambiguous: name likely accepted (validation would fire first)
        if "400" in err or "BadRequest" in type(e).__name__:
            # BadRequest that isn't invalid-name → still reject this style for safety
            return False, err
        return True, err


async def test_llm_connection(cfg: LLMSettings) -> dict:
    """Chat ping + tool-name style probe; persist compat when chat_ok.

    Hardening: LiteLLM blocking work runs in a worker thread so a hung
    test cannot freeze the runtime event loop.
    """
    import asyncio

    if not cfg.base_url or not cfg.api_key or not cfg.model:
        return {"ok": False, "error": "base_url / api_key / model 均需填写"}

    model = normalize_model_for_gateway(cfg.model, cfg.base_url)

    def _blocking_probe() -> dict:
        import litellm

        sample = ""
        try:
            resp = litellm.completion(
                model=model,
                api_base=cfg.base_url,
                api_key=cfg.api_key,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=20,
            )
            sample = (resp.choices[0].message.content or "")[:50]
            chat_ok = True
            chat_err = ""
        except Exception as e:  # noqa: BLE001
            chat_ok = False
            chat_err = str(e)[:300]
            return {
                "ok": False,
                "error": chat_err,
                "compat": None,
                "persist": False,
            }

        notes: list[str] = []
        colon_ok, colon_err = _probe_tool_name(
            model=model,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            function_name="tool:probe_ping",
        )
        notes.append(
            f"colon={'ok' if colon_ok else 'reject'}"
            + (f" ({colon_err[:80]})" if colon_err and not colon_ok else "")
        )
        under_ok, under_err = _probe_tool_name(
            model=model,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            function_name="tool_probe_ping",
        )
        notes.append(
            f"underscore={'ok' if under_ok else 'reject'}"
            + (f" ({under_err[:80]})" if under_err and not under_ok else "")
        )

        style: str
        tools_supported: bool
        if under_ok:
            style, tools_supported = "underscore", True
        elif colon_ok:
            style, tools_supported = "colon", True
        else:
            dot_ok, dot_err = _probe_tool_name(
                model=model,
                api_base=cfg.base_url,
                api_key=cfg.api_key,
                function_name="tool.probe_ping",
            )
            notes.append(
                f"dot={'ok' if dot_ok else 'reject'}"
                + (f" ({dot_err[:80]})" if dot_err and not dot_ok else "")
            )
            if dot_ok:
                style, tools_supported = "dot", True
            else:
                style, tools_supported = "none", False

        compat = build_compat(
            cfg=cfg,
            chat_ok=chat_ok,
            tools_supported=tools_supported,
            tool_name_style=style,  # type: ignore[arg-type]
            notes="; ".join(notes),
        )
        return {
            "ok": True,
            "sample": sample or "ok",
            "compat": compat.model_dump(),
            "persist": True,
            "compat_obj": compat,
        }

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_blocking_probe), timeout=90
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": "测试超时（90s）——网关无响应，已与 runtime 隔离"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}

    if result.get("persist") and result.get("compat_obj") is not None:
        existing = load_llm_settings()
        # Merge into saved credentials if caller only tested without save
        merged = LLMSettings(
            base_url=cfg.base_url or existing.base_url,
            api_key=cfg.api_key or existing.api_key,
            model=cfg.model or existing.model,
            compat=result["compat_obj"],
        )
        # Recompute fingerprint against what we actually persist
        merged.compat = build_compat(
            cfg=merged,
            chat_ok=bool(result["compat_obj"].chat_ok),
            tools_supported=bool(result["compat_obj"].tools_supported),
            tool_name_style=result["compat_obj"].tool_name_style,
            notes=result["compat_obj"].notes,
        )
        save_llm_settings(merged)
        result["compat"] = merged.compat.model_dump() if merged.compat else None

    result.pop("compat_obj", None)
    result.pop("persist", None)
    return result


__all__ = [
    "LLMSettings",
    "GatewayCompat",
    "CompatError",
    "load_llm_settings",
    "save_llm_settings",
    "test_llm_connection",
    "normalize_model_for_gateway",
    "llm_message_text",
    "compat_valid",
    "compat_public_dict",
    "require_tools_compat",
    "invalidate_compat",
    "is_invalid_function_name_error",
    "settings_fingerprint",
]
