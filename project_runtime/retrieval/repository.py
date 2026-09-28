"""RetrievalDocumentRepository — index lifecycle (WP-024).

Lifecycle (WP-024 索引生命周期):
    Source Created  → index
    Source Updated  → deactivate old + index new (acceptance §8: 旧内容
                      不再作为 ACTIVE 结果)
    Source Deleted  → deactivate
    content_hash unchanged → skip (acceptance §7)
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.retrieval.models import RetrievalDocument


class RetrievalRepositoryError(Exception):
    pass


def content_hash_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RetrievalDocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(
        self,
        *,
        project_id: int,
        source: str,
        source_id: str,
        title: str = "",
        path: str = "",
        content: str,
        doc_type: str | None = None,
        extra: dict | None = None,
    ) -> RetrievalDocument | None:
        """Create or replace a document. Returns None when content unchanged
        (skip semantics, acceptance §7)."""
        chash = content_hash_of(content)

        # Unchanged content on the ACTIVE doc → skip
        current = await self._active_doc(source, source_id, project_id)
        if current is not None and current.content_hash == chash:
            return None

        # Updated source: deactivate the old ACTIVE doc (§8)
        if current is not None:
            current.active = 0
            await self._session.flush()

        doc = RetrievalDocument(
            project_id=project_id,
            source=source,
            source_id=source_id,
            version=1 if current is None else current.version + 1,
            title=title,
            path=path,
            content_hash=chash,
            content=content,
            doc_type=doc_type,
            extra_json=json.dumps(extra, ensure_ascii=False) if extra else None,
            active=1,
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def deactivate(self, source: str, source_id: str, project_id: int) -> int:
        """Source deleted → its docs stop being ACTIVE results."""
        result = await self._session.execute(
            update(RetrievalDocument)
            .where(
                RetrievalDocument.source == source,
                RetrievalDocument.source_id == source_id,
                RetrievalDocument.project_id == project_id,
                RetrievalDocument.active == 1,
            )
            .values(active=0)
        )
        await self._session.flush()
        return result.rowcount or 0

    async def _active_doc(
        self, source: str, source_id: str, project_id: int
    ) -> RetrievalDocument | None:
        stmt = (
            select(RetrievalDocument)
            .where(
                RetrievalDocument.source == source,
                RetrievalDocument.source_id == source_id,
                RetrievalDocument.project_id == project_id,
                RetrievalDocument.active == 1,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def iter_active(self, project_id: int | None = None):
        """Yield active docs (for (re)building the FTS index)."""
        stmt = select(RetrievalDocument).where(RetrievalDocument.active == 1)
        if project_id is not None:
            stmt = stmt.where(RetrievalDocument.project_id == project_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        for r in rows:
            yield r
