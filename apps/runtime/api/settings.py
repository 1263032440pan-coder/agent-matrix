"""LLM settings API — read/write config + connectivity test."""

from fastapi import APIRouter

from project_runtime.llm import (
    LLMSettings,
    load_llm_settings,
    save_llm_settings,
    test_llm_connection,
)

router = APIRouter(prefix="/api/settings/llm", tags=["settings"])


def _masked(cfg: LLMSettings) -> dict:
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "api_key_set": bool(cfg.api_key),
        "api_key_preview": (cfg.api_key[:6] + "…") if cfg.api_key else "",
    }


@router.get("")
async def get_llm_settings():
    return _masked(load_llm_settings())


@router.put("")
async def put_llm_settings(req: LLMSettings):
    # Empty api_key means "keep existing"
    if not req.api_key:
        existing = load_llm_settings()
        req.api_key = existing.api_key
    save_llm_settings(req)
    return _masked(req)


@router.post("/test")
async def test_connection(req: LLMSettings):
    if not req.api_key:
        req.api_key = load_llm_settings().api_key
    return await test_llm_connection(req)
