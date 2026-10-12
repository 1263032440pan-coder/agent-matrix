"""ExaProvider — keyless MCP web search (规范 19 §3.2, opencode 同款).

POST https://mcp.exa.ai/mcp (MCP JSON-RPC over SSE)
  tools/call web_search_exa {query, type=auto, numResults,
  livecrawl=fallback, contextMaxCharacters}

必须带浏览器 User-Agent（实测 urllib 默认 UA → 403）。
服务端返回 LLM 优化文本；客户端只做轻量段落解析（容错优先——
解析失败返回空结果落兜底，绝不抛异常打断研究链，规范 19 §3.2）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

_MCP_URL = "https://mcp.exa.ai/mcp"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TIMEOUT = 25  # opencode 同值
_COOLDOWN_S = 60  # 连续失败冷却（Retry-After 简化版）
_MAX_RESULTS = 6


class ExaProvider:
    """Keyless Exa search — RESEARCH/READ_ONLY/priority=50."""

    name = "search:exa"
    capability = None  # 组合时填（避免循环 import 在类体里直接引）
    permission = "READ_ONLY"
    priority = 50
    enabled = True

    _fail_streak = 0
    _cooldown_until = 0.0

    def __init__(self) -> None:
        # 延迟 import 破循环
        from project_runtime.plugin.capability import CapabilityType

        ExaProvider.capability = CapabilityType.RESEARCH

    async def health(self) -> bool:
        return time.monotonic() >= self._cooldown_until

    async def invoke(self, payload: dict) -> list[dict]:
        """payload: {query, num_results?} → findings [{source,title,content}]。

        任何失败（网络/协议/解析）返回空列表——调用方（registry 上层
        编排）负责落下一优先级 provider。绝不抛异常（规范 19 §3.2
        解析容错）。
        """
        query = (payload.get("query") or "").strip()
        if not query:
            return []
        if time.monotonic() < self._cooldown_until:
            return []

        n = min(int(payload.get("num_results") or _MAX_RESULTS), 10)
        # 服务端间歇性空结果是正常抖动（实测同查询 3 次第 3 次才有结果）——
        # 重试一次再定性；空结果不计 fail_streak（只有异常才计，防误熔断）
        try:
            results = await self._search(query, n)
            ExaProvider._fail_streak = 0
            if not results:
                results = await self._search(query, n)
        except Exception:  # noqa: BLE001
            ExaProvider._fail_streak += 1
            if ExaProvider._fail_streak >= 3:
                ExaProvider._cooldown_until = (
                    time.monotonic() + _COOLDOWN_S
                )
                ExaProvider._fail_streak = 0
                logger.warning(
                    "ExaProvider: 3 consecutive call failures — cooling down %ss",
                    _COOLDOWN_S,
                )
            return []
        return results

    # ── 内部 ─────────────────────────────────────────────────────────
    async def _search(self, query: str, n: int) -> list[dict]:
        def _blocking() -> str | None:
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "web_search_exa",
                    "arguments": {
                        "query": query,
                        "type": "auto",
                        "numResults": n,
                        "livecrawl": "fallback",
                        "contextMaxCharacters": 8000,
                    },
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                _MCP_URL, data=body,
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    "User-Agent": _UA,
                },
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return r.read().decode("utf-8", errors="replace")

        raw = await asyncio.wait_for(
            asyncio.to_thread(_blocking), timeout=_TIMEOUT + 5
        )
        return self._parse_sse(raw)

    @staticmethod
    def _parse_sse(raw: str) -> list[dict]:
        """SSE `data:` 行 → content text → Title:/URL: 段落 → findings。

        全段容错（规范 19 §3.2 解析容错条款）: 上游格式改版返回 []
        而非异常——连续空结果即格式漂移信号（fail_streak 机制接手）。
        限流响应（HTTP 200 + "rate limit" 文案）→ 返回空并触发冷却。
        """
        try:
            text: str | None = None
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    d = json.loads(line[6:])
                    content = (d.get("result") or {}).get("content") or []
                    if content and isinstance(content[0], dict):
                        text = content[0].get("text")
                        break
            if not text:
                return []
            # 限流识别: Exa 免费 MCP 限流时返回 200 + 此文案
            # （非 429——不能靠状态码识别，2026-10-09 实测）
            if "rate limit" in text.lower():
                logger.warning(
                    "ExaProvider: keyless rate limit hit — cooling down"
                )
                ExaProvider._cooldown_until = (
                    time.monotonic() + _COOLDOWN_S
                )
                return []
            # 段落解析: "Title: x\nURL: y\n...\nHighlights:\n..."
            findings: list[dict] = []
            blocks = text.split("Title: ")
            for b in blocks[1:]:
                try:
                    rest = b.split("\n", 1)
                    title = rest[0].strip()[:150]
                    body = rest[1] if len(rest) > 1 else ""
                    url = ""
                    for ln in body.split("\n"):
                        if ln.startswith("URL: "):
                            url = ln[5:].strip()[:300]
                            break
                    # 内容 = 去 URL/Published/Author/Title 元数据行的正文
                    content_lines = [
                        ln for ln in body.split("\n")
                        if not ln.startswith(("URL: ", "Published: ",
                                              "Author: ", "Highlights:"))
                        and ln.strip()
                    ]
                    content = "\n".join(content_lines)[:400]
                    if title:
                        findings.append({
                            "source": url or "exa",
                            "title": title,
                            "content": content,
                        })
                except Exception:  # noqa: BLE001 — 单块坏不拖整批
                    continue
            return findings
        except Exception:  # noqa: BLE001 — 解析层全兜
            logger.debug("exa sse parse failed", exc_info=True)
            return []
