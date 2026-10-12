"""ScrapeProvider — DDG/Bing HTML 抓取兜底（规范 19 §3.3，迁移自 executor）.

行为与原 WebSearchProvider 完全一致（Bing 优先 + 中文 URL 编码 +
b_algo 解析），仅包装为 D16 Provider 契约。priority=100 兜底。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

MAX_RESULTS = 6
SNIPPET_MAX = 400
HTTP_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class ScrapeProvider:
    """DDG/Bing 页面抓取 — RESEARCH/READ_ONLY/priority=100（兜底）。"""

    name = "search:scrape"
    capability = None
    permission = "READ_ONLY"
    priority = 100
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        ScrapeProvider.capability = CapabilityType.RESEARCH

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> list[dict]:
        """payload: {query, num_results?} → findings。同步抓取放线程。"""
        import asyncio

        query = (payload.get("query") or "").strip()[:200]
        if not query:
            return []
        n = min(int(payload.get("num_results") or MAX_RESULTS), 10)
        return await asyncio.to_thread(self._search_sync, query, n)

    # ── 同步抓取（原 WebSearchProvider 行为原样迁移）────────────────
    def _search_sync(self, query: str, n: int) -> list[dict]:
        for engine in (self._bing, self._ddg):
            try:
                results = engine(query, n)
                if results:
                    return results
            except Exception as e:  # noqa: BLE001 — 引擎间兜底
                logger.debug("engine %s failed: %s", engine.__name__, e)
        return []

    def _ddg(self, query: str, n: int) -> list[dict]:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        html = self._fetch(url)
        items = []
        blocks = re.split(r'result__body', html)
        for b in blocks[1:]:
            link = re.search(
                r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                b, re.S,
            )
            if not link:
                continue
            href, title = link.group(1), re.sub(r"<[^>]+>", "", link.group(2))
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
            if href.startswith("//"):
                href = "https:" + href
            href = href.replace("&amp;", "&")
            snip = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S
            )
            snippet = (
                re.sub(r"<[^>]+>", "", snip.group(1)).strip()
                if snip else ""
            )
            if title.strip():
                items.append({
                    "source": href[:300],
                    "title": title.strip()[:150],
                    "content": snippet[:SNIPPET_MAX],
                })
            if len(items) >= n:
                break
        return items

    def _bing(self, query: str, n: int) -> list[dict]:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        html = self._fetch(url)
        items = []
        blocks = re.split(r'<li class="b_algo"', html)
        for b in blocks[1:]:
            link = re.search(
                r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', b, re.S
            )
            if not link:
                continue
            href, title = link.groups()
            title = re.sub(r"<[^>]+>", "", title).strip()
            snip = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            snippet = (
                re.sub(r"<[^>]+>", "", snip.group(1)).strip()
                if snip else ""
            )
            if title and not href.startswith("/"):
                items.append({
                    "source": href.replace("&amp;", "&")[:300],
                    "title": title[:150],
                    "content": snippet[:SNIPPET_MAX],
                })
            if len(items) >= n:
                break
        return items

    @staticmethod
    def _fetch(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
