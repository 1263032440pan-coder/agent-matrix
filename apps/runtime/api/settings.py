"""LLM settings API — read/write config + connectivity / compat probe."""

from fastapi import APIRouter

from project_runtime.llm import (
    LLMSettings,
    compat_public_dict,
    invalidate_compat,
    load_llm_settings,
    save_llm_settings,
    settings_fingerprint,
    test_llm_connection,
)

router = APIRouter(prefix="/api/settings/llm", tags=["settings"])


def _masked(cfg: LLMSettings) -> dict:
    out = {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "api_key_set": bool(cfg.api_key),
        "api_key_preview": (cfg.api_key[:6] + "…") if cfg.api_key else "",
    }
    out.update(compat_public_dict(cfg))
    return out


@router.get("")
async def get_llm_settings():
    return _masked(load_llm_settings())


@router.put("")
async def put_llm_settings(req: LLMSettings):
    existing = load_llm_settings()
    # Empty api_key means "keep existing"
    if not req.api_key:
        req.api_key = existing.api_key

    new_fp = settings_fingerprint(req.base_url, req.model, req.api_key)
    if existing.compat is not None and existing.compat.fingerprint == new_fp:
        req.compat = existing.compat
    elif existing.compat is not None:
        req.compat = existing.compat.model_copy(deep=True)
        invalidate_compat(req)
    else:
        req.compat = None

    save_llm_settings(req)
    return _masked(req)


@router.post("/test")
async def test_connection(req: LLMSettings):
    if not req.api_key:
        req.api_key = load_llm_settings().api_key
    result = await test_llm_connection(req)
    saved = load_llm_settings()
    result.update(compat_public_dict(saved))
    return result
