"""Retrieval Capability Extension (WP-025 / 08-规范).

把检索实现统一接入 retrieval.providers Extension Point:

    RetrievalOrchestrator (Core: routing / normalize / dedup / fusion)
        └─ ExtensionRegistry[RETRIEVAL] ← FTS5RetrievalProvider [Core]
                                          ← SemanticRetrievalProvider [Optional Plugin]
                                          ← RemoteRetrievalProvider [Optional]
                                          ← GraphRetrievalProvider [Future]

Core owns Source/Version/Lifecycle/Scope/Filter/Normalize/Dedup/Fusion;
Providers own only the retrieval algorithm (WP-025 分工).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.extension.registry import (
    CapabilityType,
    ExtensionRegistry,
    Permission,
)
from project_runtime.retrieval.fts import FTS5Retriever
from project_runtime.retrieval.merger import RetrievalMerger

logger = logging.getLogger(__name__)


class FTS5RetrievalProvider:
    """Core retrieval provider (WP-024 FTS5 + BM25) under the unified
    Provider Contract. Registered like any other provider — no special
    casing in Core."""

    name = "fts5-retrieval"
    capability = CapabilityType.RETRIEVAL
    permission = Permission.READ_ONLY
    priority = 100  # core provider outranks optional plugins by default
    enabled = True

    def __init__(self, session: AsyncSession):
        self._session = session

    async def health(self) -> bool:
        return True  # FTS5 lives in the same SQLite file — always healthy

    async def invoke(self, payload: dict[str, Any]) -> list[dict]:
        """payload: {query, project_id?, doc_type?, limit?} → normalized hits."""
        retriever = FTS5Retriever(self._session)
        return await retriever.search(
            payload["query"],
            project_id=payload.get("project_id"),
            doc_type=payload.get("doc_type"),
            limit=payload.get("limit", 10),
        )


class RetrievalOrchestrator:
    """Core-side orchestration over the retrieval.providers point.

    Routes a query to ALL enabled RETRIEVAL providers in the unified
    registry, then merges (normalize + deduplicate + fusion) — Core
    responsibilities per WP-025. New providers require NO Core changes:
    register into the registry and they participate automatically
    (acceptance §2).
    """

    def __init__(self, session: AsyncSession, registry: ExtensionRegistry):
        self._session = session
        self._registry = registry
        self._merger = RetrievalMerger()

    async def search(
        self,
        query: str,
        *,
        project_id: int | None = None,
        doc_type: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Query every enabled RETRIEVAL provider; merge results.

        Provider failures are isolated: a crashing provider contributes
        nothing but never breaks the search (Core robustness).
        """
        payload = {
            "query": query, "project_id": project_id,
            "doc_type": doc_type, "limit": limit,
        }
        provider_results: list[list[dict]] = []
        providers_used: list[str] = []
        errors: list[dict] = []

        for info in self._registry.list_providers(CapabilityType.RETRIEVAL):
            if not info.enabled or info.provider is None:
                continue
            try:
                hits = await info.provider.invoke(payload)
                if isinstance(hits, list):
                    provider_results.append(hits)
                    providers_used.append(info.name)
            except Exception as e:  # noqa: BLE001 — provider isolation
                logger.warning(
                    "Retrieval provider %s failed: %s", info.name, e
                )
                errors.append({"provider": info.name, "error": str(e)[:120]})

        merged = self._merger.merge(*provider_results)[:limit]
        return {
            "query": query,
            "providers": providers_used,
            "results": merged,
            "total": len(merged),
            "errors": errors,
        }
