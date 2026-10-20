"""LLM Capability Provider（AOP-P2-2 清偿——20号规范批次 3）.

债的来源（AOP 第三轮返修单）: SkillSelector 与 Worker（llm_worker/
llm_extractor）各自直调 litellm——LLM 能力域绕过 ExtensionRegistry，
债随 Agent 数 N 放大。

清偿形态（规范 §10.3 原文: "顺手把 litellm 直调抽为
CapabilityType.LLM Provider 是最便宜时机"）:
  - LiteLLMProvider: D16 协议实现，capability=LLM
  - invoke(payload) 统一入口: completion（含 tools/tool_choice）与
    流式（stream=True 返回 chunk 迭代器）
  - Provider 走 shared_registry（组合根单例——与 search providers/
    worker tools 同一注册面）
  - **零行为变更**: 现有调用点换 provider.invoke(...)，配置读取、
    网关归一化、错误语义全部原样保留（清偿≠重构调用语义）

降级纪律: LLM Provider 不可用（未配置/网络失败）→ 返回
{"ok": False, "error": ...}——调用方各自维持既有降级链
（selector→关键词计分; worker→裸跑）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LiteLLMProvider:
    """litellm 单一封装（capability=LLM——进程内唯一直调点）。

    invoke payload:
      mode: "completion" | "acompletion"
      model / api_base / api_key / messages / tools / tool_choice /
      max_tokens / timeout / stream / temperature
    返回: {"ok": True, "response": <litellm 原样 dict/迭代器>} 或
          {"ok": False, "error": str}
    """

    name = "llm:litellm"
    permission = "READ_ONLY"
    priority = 100
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        self.capability = CapabilityType.LLM

    async def health(self) -> bool:
        from project_runtime.llm import load_llm_settings

        cfg = load_llm_settings()
        return bool(cfg.base_url and cfg.api_key and cfg.model)

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if payload.get("mode") == "acompletion":
                coro = self._acompletion(payload)
            else:
                coro = asyncio.to_thread(self._completion, payload)
            resp = await coro
            return {"ok": True, "response": resp}
        except Exception as e:  # noqa: BLE001 — 降级语义统一
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── 直调点（进程内唯二 litellm import）──────────────────────

    @staticmethod
    def _completion(payload: dict) -> Any:
        import litellm

        kwargs = {k: v for k, v in payload.items() if k != "mode"}
        return litellm.completion(**kwargs)

    @staticmethod
    async def _acompletion(payload: dict) -> Any:
        import litellm

        kwargs = {k: v for k, v in payload.items() if k != "mode"}
        return await litellm.acompletion(**kwargs)


def get_llm_provider():
    """shared_registry 里的 LLM Provider（未注册则即时注册并返回）。

    调用点便捷面——selector/worker 换装时的唯一依赖入口。
    """
    from project_runtime.extension.shared import shared_registry

    reg = shared_registry()
    info = reg._providers.get(LiteLLMProvider.name)  # noqa: SLF001
    if info is None or not info.enabled:
        provider = LiteLLMProvider()
        reg.register(provider)
        return provider
    return info.provider


async def llm_complete(**kwargs) -> dict[str, Any]:
    """便捷入口: 同步 completion（阻塞跑线程）。"""
    return await get_llm_provider().invoke({"mode": "completion", **kwargs})


async def llm_acomplete(**kwargs) -> dict[str, Any]:
    """便捷入口: 异步 completion（流式传 stream=True）。"""
    return await get_llm_provider().invoke({"mode": "acompletion", **kwargs})
