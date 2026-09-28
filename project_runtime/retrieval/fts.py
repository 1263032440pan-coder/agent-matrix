"""FTS5 index + BM25 retriever (WP-024 固定技术).

The FTS index is a SQLite virtual table in the SAME database, kept in sync
with retrieval_document by the indexer (upsert mirrors the lifecycle:
skip-unchanged / replace-on-update / deactivate-on-delete).

Search = FTS5 MATCH (BM25 ranking) joined against ACTIVE docs with
project_id isolation + SQL metadata filter (fixed tech, no vector).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

FTS_TABLE = "retrieval_fts"
# fts rowid == retrieval_document.id for direct join
_DDL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    title, content,
    tokenize='unicode61'
)
"""


def cjk_tokenize(text: str) -> str:
    """CJK-aware pre-tokenization for FTS5 (unicode61 has NO CJK segmentation
    — a whole Chinese sentence becomes one unmatchable token).

    Strategy: emit character bigrams for CJK runs, keep latin words intact.
    Symmetric on index and query sides. Still pure SQLite FTS5 (fixed tech).
    """
    out: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            out.append("".join(buf))
            buf.clear()

    for ch in text:
        if "一" <= ch <= "鿿":
            flush()
            out.append(ch)
        elif ch.isalnum() or ch in "_-":
            buf.append(ch)
        else:
            flush()
    flush()

    # char-unigrams → bigrams for CJK sequences (bigram matches cover both
    # exact substrings and partial overlaps; latin words stay whole)
    tokens: list[str] = []
    i = 0
    while i < len(out):
        t = out[i]
        if len(t) == 1 and "一" <= t <= "鿿":
            j = i
            cjk_run = []
            while j < len(out) and len(out[j]) == 1 and "一" <= out[j] <= "鿿":
                cjk_run.append(out[j])
                j += 1
            for k in range(len(cjk_run) - 1):
                tokens.append(cjk_run[k] + cjk_run[k + 1])
            if len(cjk_run) == 1:
                tokens.append(cjk_run[0])
            i = j
        else:
            tokens.append(t)
            i += 1
    return " ".join(tokens)


async def ensure_fts_table(session: AsyncSession) -> None:
    await session.execute(text(_DDL))
    await session.commit()


# NOTE: indexer methods deliberately do NOT commit — the session lifecycle
# belongs to the caller (avoids ORM expiry surprises mid-flow).


class FTS5Indexer:
    """Mirrors repository lifecycle into the FTS virtual table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def index(self, doc_id: int, title: str, content: str) -> None:
        await self._session.execute(
            text(
                f"INSERT INTO {FTS_TABLE}(rowid, title, content) "
                "VALUES (:id, :title, :content)"
            ),
            {
                "id": doc_id,
                "title": cjk_tokenize(title),
                "content": cjk_tokenize(content),
            },
        )

    async def replace(self, doc_id: int, title: str, content: str) -> None:
        # Update = drop old rows + insert current (content-addressed mirror)
        await self.remove(doc_id)
        await self.index(doc_id, title, content)  # content tokenized inside

    async def remove(self, doc_id: int) -> None:
        await self._session.execute(
            text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :id"), {"id": doc_id}
        )


class FTS5Retriever:
    """BM25 search with project isolation + metadata filter."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def search(
        self,
        query: str,
        *,
        project_id: int | None = None,
        doc_type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return ACTIVE matches ranked by BM25 (FTS5 default order).

        project_id isolation is enforced in SQL (acceptance §2); doc_type is
        the SQL metadata filter (§3).
        """
        where = ["d.active = 1"]
        # Symmetric CJK tokenization + per-token quoting (also neutralizes
        # FTS query syntax like NEAR/OR injected via user input)
        toks = cjk_tokenize(query).split()
        safe_q = " ".join(f'"{t}"' for t in toks) if toks else query
        params: dict = {"q": safe_q, "lim": limit}
        if project_id is not None:
            where.append("d.project_id = :pid")
            params["pid"] = project_id
        if doc_type is not None:
            where.append("d.doc_type = :dt")
            params["dt"] = doc_type

        sql = f"""
        SELECT d.id, d.project_id, d.source, d.source_id, d.version,
               d.title, d.path, d.doc_type,
               snippet({FTS_TABLE}, 1, '«', '»', '…', 12) AS snippet,
               bm25({FTS_TABLE}) AS score
        FROM {FTS_TABLE} f
        JOIN retrieval_document d ON d.id = f.rowid
        WHERE {FTS_TABLE} MATCH :q AND {' AND '.join(where)}
        ORDER BY score
        LIMIT :lim
        """
        rows = (
            (await self._session.execute(text(sql), params)).mappings().all()
        )
        return [
            {
                "id": r["id"], "project_id": r["project_id"],
                "source": r["source"], "source_id": r["source_id"],
                "version": r["version"], "title": r["title"],
                "path": r["path"], "doc_type": r["doc_type"],
                "snippet": r["snippet"], "score": float(r["score"]),
                "provider": "fts5",
            }
            for r in rows
        ]
