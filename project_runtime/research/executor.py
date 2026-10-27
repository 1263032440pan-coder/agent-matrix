"""Web Research Executor (偏离②③ 兑现 / SYS-033 后半).

链路:
  ResearchRecord(OPEN, question)
    → WebSearchProvider（DuckDuckGo HTML 抓取为主 + Bing 兜底，无 API key）
    → LLM 综合器（搜索结果 → 结构化 findings + summary）
    → ResearchRecord(ANSWERED, findings_json + summary)

红线延续:
  - PRIVATE / MANDATORY 路由已在 GapResolver 挡住——本模块只被
    PROJECT_METHOD/DOMAIN/TECHNICAL 路由调用
  - 检索结果标注非事实（与 ContextBuilder 同款语义）
  - 每次研究前查既有（(question, kind) 唯一约束兜底）
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.research.models import ResearchRecord

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_RESULTS = 6
SNIPPET_MAX = 400
HTTP_TIMEOUT = 12


class WebSearchError(Exception):
    pass


class WebSearchProvider:
    """无 key 搜索抓取（DuckDuckGo HTML 版 + Bing 兜底）。"""

    def search(self, query: str, n: int = MAX_RESULTS) -> list[dict]:
        query = query.strip()[:200]
        if not query:
            return []
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
        # 结果块: <a rel="nofollow" class="result__a" href="...">标题</a>
        # + <a class="result__snippet">摘要</a>
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
            # DDG 重定向解包（uddg= 参数）+ 协议相对地址补全
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
            if href.startswith("//"):
                href = "https:" + href
            # HTML 实体解码
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
            # 跳过 Bing 内部跳转/空标题
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


async def _llm_synthesize(question: str, results: list[dict]) -> dict | None:
    """搜索结果 → LLM 综合成结构化 findings（best-effort）。

    LLM 不可用时回退：直接用首条摘要拼 summary。
    """
    if not results:
        return None
    context = "\n\n".join(
        f"[{i+1}] {r['title']}\n{r['content']}" for i, r in enumerate(results)
    )
    # 快照进线程（避免 to_thread 内触碰 ORM —— MissingGreenlet 老坑）
    question_text = question
    try:
        import asyncio

        from project_runtime.llm import (
            load_llm_settings,
            normalize_model_for_gateway,
        )

        # AOP-P2-2 清偿: litellm 直调 → LLM Capability Provider
        from project_runtime.llm.provider import llm_complete

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            raise RuntimeError("LLM not configured")
        model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)

        def _answer(resp) -> str:
            m = resp.choices[0].message
            return m.content or (getattr(m, "reasoning_content", "") or "")[-800:]

        result = await asyncio.wait_for(
            llm_complete(
                model=model_id,
                api_base=cfg.base_url,
                api_key=cfg.api_key,
                messages=[{
                    "role": "user",
                    "content": (
                        f"研究问题：{question_text}\n\n以下是搜索结果：\n{context}\n\n"
                        "请综合以上信息，用中文回答该问题（300 字内），"
                        "并标注信息来自搜索（非项目内事实）。"
                    ),
                }],
                max_tokens=2000,
                timeout=120,
            ),
            timeout=130,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "LLM failed"))
        answer = _answer(result["response"])
        return {
            "summary": answer.strip()[:1200],
            "findings": [
                {"source": r["source"], "title": r["title"],
                 "content": r["content"]}
                for r in results
            ],
            "synthesized": True,
        }
    except Exception as e:  # noqa: BLE001 — LLM 是增强不是依赖
        logger.debug("LLM synthesize failed: %s", e)
        return {
            "summary": "【搜索参考·非事实】" + " / ".join(
                r["title"] for r in results[:3]
            )[:600],
            "findings": [
                {"source": r["source"], "title": r["title"],
                 "content": r["content"]}
                for r in results
            ],
            "synthesized": False,
        }


class WebResearchExecutor:
    """执行 OPEN 的 ResearchRecord → 搜索 → LLM 综合 → ANSWERED。

    搜索走共享 ExtensionRegistry 的 RESEARCH provider（规范 19 §3.4，
    AOP-P2-1 清偿）: Exa keyless 优先 → scrape 兜底 → 全失败空结果
    （研究保持 OPEN 可重试）。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        # 搜索 provider 集合（AOP-P2-1: 经 registry，不直连实现）
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )

        self._registry = shared_registry()
        register_search_providers(self._registry)
        # 20号规范 §4.3 三能力①: TOOL 插件面注册（幂等——研究链同根）
        try:
            from project_runtime.worker.coding_tools import (
                register_batch3_tools,
            )
            from project_runtime.worker.tools import register_worker_tools

            register_worker_tools(self._registry)
            register_batch3_tools(self._registry)
        except Exception:  # noqa: BLE001 — 插件面失败不阻断搜索
            pass

    async def _search(self, query: str) -> list[dict]:
        """经 registry 按 priority 试 provider——全失败返回空。"""
        from project_runtime.plugin.capability import CapabilityType

        providers = self._registry.list_providers(CapabilityType.RESEARCH)
        for info in providers:
            if not info.enabled:
                continue
            try:
                results = await info.provider.invoke({"query": query})
                if results:
                    return results
            except Exception:  # noqa: BLE001 — provider 间兜底
                logger.debug(
                    "search provider %s failed", info.name, exc_info=True
                )
        return []

    async def execute_research(self, research_id: int) -> ResearchRecord | None:
        rec = await self._session.get(ResearchRecord, research_id)
        if rec is None:
            return None
        if rec.status == "ANSWERED":
            return rec  # 五查幂等——已答不重做

        # 返修V3 批B: 专用研究类型分派
        if rec.research_kind == "SKILL_DISCOVERY":
            from project_runtime.skills.discovery import (
                execute_skill_discovery_record,
            )

            await execute_skill_discovery_record(self._session, research_id)
            return await self._session.get(ResearchRecord, research_id)

        if rec.research_kind == "METHODOLOGY_RESEARCH":
            from project_runtime.skills.discovery import (
                project_has_private_domain,
                run_gpt_researcher,
            )

            if await project_has_private_domain(
                self._session, rec.project_id,
            ):
                rec.summary = "PRIVATE 域跳过联网方法论研究"
                rec.findings_json = json.dumps(
                    {"stages": [], "skipped_private": True},
                    ensure_ascii=False,
                )
                rec.status = "ANSWERED"
                await self._session.flush()
                return rec
            report = await run_gpt_researcher(rec.question)
            rec.summary = str(report.get("summary") or "")[:2000]
            rec.findings_json = json.dumps(report, ensure_ascii=False)
            rec.status = "ANSWERED"
            await self._session.flush()
            return rec

        results = await self._search(rec.question)
        if not results:
            logger.warning(
                "web search empty for research %d — keep OPEN", research_id
            )
            return rec  # 保持 OPEN（下次可重试）

        synth = await _llm_synthesize(rec.question, results)
        if synth is None:
            return rec

        rec.summary = synth["summary"]
        rec.findings_json = json.dumps(
            synth["findings"], ensure_ascii=False
        )
        rec.status = "ANSWERED"
        await self._session.flush()
        logger.info(
            "Research ANSWERED: id=%d kind=%s q=%.60s (%d findings%s)",
            research_id, rec.research_kind, rec.question,
            len(synth["findings"]),
            ", LLM synthesized" if synth["synthesized"] else ", raw",
        )
        return rec

    async def execute_pending(self, limit: int = 5) -> list[int]:
        """执行项目内全部 OPEN 研究（上限 limit）。返回 ANSWERED 的 id。"""
        stmt = (
            select(ResearchRecord)
            .where(ResearchRecord.status == "OPEN")
            .order_by(ResearchRecord.id)
            .limit(limit)
        )
        recs = (await self._session.execute(stmt)).scalars().all()
        answered: list[int] = []
        for r in recs:
            done = await self.execute_research(r.id)
            if done is not None and done.status == "ANSWERED":
                answered.append(r.id)
        await self._session.commit()
        return answered
