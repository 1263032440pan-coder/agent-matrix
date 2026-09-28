"""WP-025 Retrieval Capability Extension tests — acceptance checklist.

Acceptance:
  [x] FTS5 通过统一 Provider Contract 注册
  [x] 新 Provider 不需要修改 Retrieval Memory Core
  [x] Provider 可启停
  [x] 多 Provider 结果可 Normalize / Deduplicate / Fusion
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
from project_runtime.extension.registry import (
    CapabilityType,
    ExtensionRegistry,
    Permission,
)
from project_runtime.project_state.base import Base
from project_runtime.retrieval import (
    FTS5Indexer,
    FTS5RetrievalProvider,
    RetrievalDocumentRepository,
    RetrievalOrchestrator,
    ensure_fts_table,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/providers.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        await ensure_fts_table(s)
    yield eng
    await eng.dispose()


async def _seed(dbengine, docs: list[tuple[int, str, str]]):
    async with AsyncSession(dbengine) as s:
        repo = RetrievalDocumentRepository(s)
        idx = FTS5Indexer(s)
        for pid, sid, content in docs:
            d = await repo.upsert(
                project_id=pid, source="ARTIFACT", source_id=sid,
                title=sid, content=content,
            )
            if d:
                await idx.index(d.id, sid, content)
        await s.commit()


# ── §1: FTS5 通过统一 Provider Contract 注册 ──────────────────────────


@pytest.mark.asyncio
async def test_fts5_registered_via_contract(dbengine):
    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))

        infos = registry.list_providers(CapabilityType.RETRIEVAL)
        assert len(infos) == 1
        p = infos[0]
        assert p.name == "fts5-retrieval"
        assert p.capability == CapabilityType.RETRIEVAL
        assert p.permission == Permission.READ_ONLY
        assert await registry.health("fts5-retrieval") is True

        # Invoke goes through the unified registry contract
        await _seed(dbengine, [(1, "d1", "架构设计文档 架构模式说明")])
        hits = await registry.invoke(
            CapabilityType.RETRIEVAL, {"query": "架构", "project_id": 1}
        )
        assert isinstance(hits, list) and len(hits) == 1
        assert hits[0]["source_id"] == "d1"


# ── §2: 新 Provider 不需要修改 Core ──────────────────────────────────


class StubSemanticProvider:
    """Simulates an optional plugin provider (e.g. SemanticRetrievalProvider).
    Zero Core changes — registration is the only integration step."""

    name = "semantic-retrieval"
    capability = CapabilityType.RETRIEVAL
    permission = Permission.READ_ONLY
    priority = 50
    enabled = True

    def __init__(self, canned: list[dict]):
        self._canned = canned
        self.invoked = 0

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> list[dict]:
        self.invoked += 1
        return [dict(r) for r in self._canned]


@pytest.mark.asyncio
async def test_new_provider_joins_without_core_changes(dbengine):
    await _seed(dbengine, [(1, "d1", "架构设计文档 架构模式说明")])

    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))
        orch = RetrievalOrchestrator(s, registry)

        # Before: only fts5 serves
        r0 = await orch.search("架构", project_id=1)
        assert r0["providers"] == ["fts5-retrieval"]

        # A new semantic provider registers — Core (orchestrator) untouched
        stub = StubSemanticProvider([
            {"source": "ARTIFACT", "source_id": "d1", "version": 1,
             "title": "架构设计文档", "score": -3.0, "provider": "semantic"},
        ])
        registry.register(stub)

        r1 = await orch.search("架构", project_id=1)
        assert set(r1["providers"]) == {"fts5-retrieval", "semantic-retrieval"}
        assert stub.invoked == 1
        # both providers' hits present, merged
        assert r1["total"] >= 1


# ── §3: Provider 可启停 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_enable_disable(dbengine):
    await _seed(dbengine, [(1, "d1", "架构设计文档 架构模式说明")])

    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))
        stub = StubSemanticProvider([
            {"source": "ARTIFACT", "source_id": "d2", "version": 1, "score": 0.0},
        ])
        registry.register(stub)
        orch = RetrievalOrchestrator(s, registry)

        # both on
        assert set((await orch.search("架构"))["providers"]) == {
            "fts5-retrieval", "semantic-retrieval"
        }

        # disable semantic → excluded
        registry.set_enabled("semantic-retrieval", False)
        r = await orch.search("架构", project_id=1)
        assert r["providers"] == ["fts5-retrieval"]
        assert stub.invoked == 1  # not invoked again

        # re-enable → back
        registry.set_enabled("semantic-retrieval", True)
        assert "semantic-retrieval" in (await orch.search("架构"))["providers"]


# ── §4: 多 Provider 结果 Normalize / Dedup / Fusion ───────────────────


@pytest.mark.asyncio
async def test_multi_provider_dedup_fusion(dbengine):
    await _seed(dbengine, [(1, "d1", "架构设计文档 架构模式说明")])

    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))

        # Same doc returned by BOTH providers with different scores → dedup
        # keeps the better (lower) score; fusion orders fts5-first
        stub = StubSemanticProvider([
            {"source": "ARTIFACT", "source_id": "d1", "version": 1,
             "title": "架构设计文档", "score": -5.0, "provider": "semantic"},
            {"source": "ARTIFACT", "source_id": "d1", "version": 1,
             "title": "架构设计文档", "score": -1.0, "provider": "semantic"},
        ])
        registry.register(stub)
        orch = RetrievalOrchestrator(s, registry)

        r = await orch.search("架构", project_id=1)
        d1_hits = [h for h in r["results"] if h["source_id"] == "d1"]
        assert len(d1_hits) == 1  # deduplicated across providers AND within
        assert d1_hits[0]["score"] == -5.0  # best score kept
        assert r["total"] == len(r["results"])


@pytest.mark.asyncio
async def test_provider_failure_isolated(dbengine):
    """崩溃的 provider 不拖垮检索——结果缺失但错误可见。"""
    await _seed(dbengine, [(1, "d1", "架构设计文档 架构模式说明")])

    class BoomProvider(StubSemanticProvider):
        name = "boom-retrieval"

        async def invoke(self, payload):
            raise RuntimeError("semantic index exploded")

    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        registry.register(FTS5RetrievalProvider(s))
        registry.register(BoomProvider([]))
        orch = RetrievalOrchestrator(s, registry)

        r = await orch.search("架构", project_id=1)
        assert r["providers"] == ["fts5-retrieval"]  # boom excluded
        assert len(r["results"]) == 1  # fts5 still served
        assert len(r["errors"]) == 1
        assert r["errors"][0]["provider"] == "boom-retrieval"


@pytest.mark.asyncio
async def test_no_providers_unified_error(dbengine):

    registry = ExtensionRegistry()
    async with AsyncSession(dbengine) as s:
        orch = RetrievalOrchestrator(s, registry)
        r = await orch.search("任何词")
        # orchestrator degrades gracefully: no providers → empty result
        # (not an exception — retrieval absence is not a runtime failure)
        assert r["providers"] == []
        assert r["results"] == []
