"""LLM settings persistence + LiteLLM client factory.

Config is stored in data/llm_settings.json (runtime-writable), overriding
env defaults. API key is stored locally and never logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/llm_settings.json")


class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


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


async def test_llm_connection(cfg: LLMSettings) -> dict:
    """Real chat completion round-trip via LiteLLM."""
    import litellm

    if not cfg.base_url or not cfg.api_key or not cfg.model:
        return {"ok": False, "error": "base_url / api_key / model 均需填写"}

    # OpenAI-compatible gateways (e.g. custom relays like MoMA) need an
    # explicit provider prefix — LiteLLM cannot infer it from the model name.
    model = cfg.model
    if cfg.base_url and "/" in model and not model.startswith("openai/"):
        known_prefixes = ("openai/", "azure/", "anthropic/", "huggingface/",
                          "ollama/", "gemini/", "deepseek/", "mistral/")
        if not model.startswith(known_prefixes):
            model = f"openai/{model}"
    try:
        resp = await litellm.acompletion(
            model=model,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=20,
        )
        text = resp.choices[0].message.content or ""
        return {"ok": True, "sample": text[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
