"""WP-024 Retrieval Memory Foundation tests — acceptance checklist (九条)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.retrieval import (
    FTS5Indexer,
    FTS5Retriever,
    PathRetriever,
    RetrievalDocumentRepository,
    RetrievalMerger,
    RipgrepRetriever,
    ensure_fts_table,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/retrieval.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        await ensure_fts_table(s)
    yield eng
    await eng.dispose()


async def _index_doc(
    session, project_id, source_id, content, title="", path="", doc_type=None,
    source="ARTIFACT",
):
    repo = RetrievalDocumentRepository(session)
    indexer = FTS5Indexer(session)
    doc = await repo.upsert(
        project_id=project_id, source=source, source_id=source_id,
        title=title, path=path, content=content, doc_type=doc_type,
    )
    if doc is not None:  # None = skipped (unchanged content)
        await indexer.index(doc.id, title, content)
    return doc  # NOTE: caller commits; doc stays live (no expiry)


# §1 文本 Artifact 可进入 FTS5
@pytest.mark.asyncio
async def test_artifact_enters_fts5(dbengine):
    async with AsyncSession(dbengine) as s:
        doc = await _index_doc(
            s, project_id=1, source_id="art-1",
            content="任务看板的核心功能包括创建任务和筛选任务",
            title="核心功能说明", path="data/tasks/1/art-1.md",
        )
        assert doc is not None and doc.version == 1
        await s.commit()

        hits = await FTS5Retriever(s).search("任务看板", project_id=1)
        assert len(hits) == 1
        assert "任务看板" in hits[0]["snippet"] or hits[0]["title"] == "核心功能说明"


# §2 project_id 隔离
@pytest.mark.asyncio
async def test_project_isolation(dbengine):
    async with AsyncSession(dbengine) as s:
        await _index_doc(s, 1, "a", "unique_token_alpha 项目一", title="A")
        await _index_doc(s, 2, "b", "unique_token_beta 项目二", title="B")
        await s.commit()

        hits1 = await FTS5Retriever(s).search("unique_token", project_id=1)
        assert [h["source_id"] for h in hits1] == ["a"]
        hits2 = await FTS5Retriever(s).search("unique_token", project_id=2)
        assert [h["source_id"] for h in hits2] == ["b"]
        # unscoped sees both
        hits_all = await FTS5Retriever(s).search("unique_token")
        assert len(hits_all) == 2


# §3 Metadata Filter
@pytest.mark.asyncio
async def test_metadata_filter(dbengine):
    async with AsyncSession(dbengine) as s:
        await _index_doc(s, 1, "doc", "shared_keyword 内容甲", title="X", doc_type="需求")
        await _index_doc(s, 1, "code", "shared_keyword 内容乙", title="Y", doc_type="代码")
        await s.commit()

        only_req = await FTS5Retriever(s).search("shared_keyword", project_id=1, doc_type="需求")
        assert {h["doc_type"] for h in only_req} == {"需求"}
        assert len(only_req) == 1


# §4 BM25 排序（多次命中词的文档排前）
@pytest.mark.asyncio
async def test_bm25_ranking(dbengine):
    async with AsyncSession(dbengine) as s:
        # doc with MORE occurrences of the term ranks first (BM25)
        await _index_doc(s, 1, "sparse", "搜索词出现一次", title="sparse")
        await _index_doc(s, 1, "dense", "搜索词 搜索词 搜索词 密集出现", title="dense")
        await s.commit()

        hits = await FTS5Retriever(s).search("搜索词", project_id=1)
        assert len(hits) == 2
        assert hits[0]["source_id"] == "dense"  # bm25 ranks dense first
        assert hits[0]["score"] < hits[1]["score"]  # lower bm25 = better


# §5/§6 文件名/路径搜索
@pytest.mark.asyncio
async def test_path_search(dbengine):
    async with AsyncSession(dbengine) as s:
        await _index_doc(
            s, 1, "p1", "内容", title="部署手册",
            path="data/tasks/1/deploy_guide.md",
        )
        await _index_doc(s, 1, "p2", "内容", title="架构", path="data/tasks/1/arch.md")
        await s.commit()

        by_name = await PathRetriever(s).search("deploy_guide", project_id=1)
        assert len(by_name) == 1 and by_name[0]["source_id"] == "p1"
        by_title = await PathRetriever(s).search("部署", project_id=1)
        assert len(by_title) == 1 and by_title[0]["source_id"] == "p1"


# §7 相同 content_hash 不重复索引
@pytest.mark.asyncio
async def test_same_content_skip(dbengine):
    async with AsyncSession(dbengine) as s:
        await _index_doc(s, 1, "same", "完全一样的内容", title="T")
        d2 = await _index_doc(s, 1, "same", "完全一样的内容", title="T")
        assert d2 is None  # skip semantics
        await s.commit()

        hits = await FTS5Retriever(s).search("完全一样", project_id=1)
        assert len(hits) == 1  # no duplicate index rows


# §8 更新后旧内容不再 ACTIVE
@pytest.mark.asyncio
async def test_update_deactivates_old(dbengine):
    async with AsyncSession(dbengine) as s:
        RetrievalDocumentRepository(s)
        indexer = FTS5Indexer(session=s)

        d1 = await _index_doc(s, 1, "u", "旧版本内容 old_version_text", title="U")
        old_id = d1.id
        d2 = await _index_doc(s, 1, "u", "新版本内容 new_version_text", title="U")
        assert d2 is not None
        # attributes restored from pre-commit snapshot by the helper
        assert d2.version == 2 and d2.id != old_id
        # FTS mirror: remove old row, index new
        await indexer.replace(d2.id, "U", "新版本内容 new_version_text")
        await indexer.remove(old_id)
        await s.commit()

        # old content must NOT be searchable
        old_hits = await FTS5Retriever(s).search("old_version_text", project_id=1)
        assert old_hits == []
        # new content is
        new_hits = await FTS5Retriever(s).search("new_version_text", project_id=1)
        assert len(new_hits) == 1 and new_hits[0]["version"] == 2


# §6 ripgrep 代码搜索（真实 rg 子进程）
@pytest.mark.asyncio
async def test_ripgrep_search(tmp_path):
    (tmp_path / "tasks").mkdir()
    ws = tmp_path / "tasks" / "7"
    ws.mkdir(parents=True)
    (ws / "app.py").write_text(
        "def kanban_handler():\n    return 'TODO'\n", encoding="utf-8"
    )
    (ws / "other.md").write_text("kanban_handler mentioned in docs", encoding="utf-8")

    rg = RipgrepRetriever(workspace_root=str(tmp_path / "tasks"))
    hits = await rg.search("kanban_handler", project_id=7)
    assert len(hits) >= 1
    py_hits = [h for h in hits if h["path"].endswith("app.py")]
    assert len(py_hits) == 1
    assert py_hits[0]["line"] == 1
    assert "def kanban_handler" in py_hits[0]["snippet"]


# §9 不引入 Embedding/Rerank/Vector —— 结构性断言
def test_no_vector_tech():
    import project_runtime.retrieval as rm

    banned = ("embedding", "vector", "rerank", "faiss", "chroma", "annoy")
    src = ""
    import inspect

    for mod in (rm.repository, rm.fts, rm.retrievers, rm.merger):
        src += inspect.getsource(mod)
    for b in banned:
        assert b not in src.lower().replace("no vector", ""), f"banned tech: {b}"


# Merger: 归一化 + 去重 + 融合
def test_merger_dedup_and_fusion():
    m = RetrievalMerger()
    fts = [
        {"source": "ARTIFACT", "source_id": "a", "version": 1, "score": -2.0},
        {"source": "ARTIFACT", "source_id": "b", "version": 1, "score": -1.0},
    ]
    path_dup = [
        {"source": "ARTIFACT", "source_id": "a", "version": 1, "score": 0.0},
    ]
    rg_hits = [
        {"source": "FILE", "path": "x.py", "line": 3},
        {"source": "FILE", "path": "x.py", "line": 3},  # dup line
    ]
    merged = m.merge(fts, path_dup, rg_hits)
    ids = [(r["source_id"], r["version"]) for r in merged if r["source"] == "ARTIFACT"]
    assert ids == [("a", 1), ("b", 1)]  # dup a kept once with BEST score (-2.0)
    assert merged[0]["score"] == -2.0
    file_hits = [r for r in merged if r["source"] == "FILE"]
    assert len(file_hits) == 1  # dup line deduped
