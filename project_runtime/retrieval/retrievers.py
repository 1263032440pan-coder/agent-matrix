"""Path / Filename retriever + ripgrep code retriever (WP-024 固定技术)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.retrieval.models import RetrievalDocument

logger = logging.getLogger(__name__)


class PathRetriever:
    """Filename / path search over ACTIVE documents (SQL LIKE, §5/§6)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def search(
        self,
        pattern: str,
        *,
        project_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        where = [RetrievalDocument.active == 1]
        if project_id is not None:
            where.append(RetrievalDocument.project_id == project_id)
        like = f"%{pattern}%"
        stmt = (
            select(RetrievalDocument)
            .where(
                *where,
                RetrievalDocument.path.like(like)
                | RetrievalDocument.title.like(like),
            )
            .order_by(RetrievalDocument.id)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id, "project_id": r.project_id, "source": r.source,
                "source_id": r.source_id, "version": r.version,
                "title": r.title, "path": r.path, "doc_type": r.doc_type,
                "snippet": r.path or r.title, "score": 0.0,
                "provider": "path",
            }
            for r in rows
        ]


class RipgrepRetriever:
    """Code search via the ripgrep binary (subprocess, bounded output).

    Fixed tech per WP-024: rg searches the on-disk artifact/task workspace.
    Returns matched file + line + snippet; no DB involvement beyond scope
    roots derived from project ids.
    """

    def __init__(self, workspace_root: str = "data/tasks"):
        self._root = Path(workspace_root)
        self._rg = shutil.which("rg")

    async def search(
        self,
        pattern: str,
        *,
        project_id: int | None = None,
        limit: int = 20,
        timeout_s: float = 10.0,
    ) -> list[dict]:
        if self._rg is None:
            return []  # rg absent → provider yields nothing (never crashes)
        scope = self._root / str(project_id) if project_id is not None else self._root
        if not scope.exists():
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._rg,
                "--no-messages", "-n", "--no-heading",
                "-m", str(limit),  # max matches per file
                "--max-count", str(limit),
                "-e", pattern,
                str(scope),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except (asyncio.TimeoutError, OSError):
            return []

        import re

        # rg --no-heading format: <path>:<line>:<content>. Windows paths
        # contain ':' (C:\...) — anchor the split on ':<digits>:'.
        line_re = re.compile(r"^(?P<path>.*?):(?P<line>\d+):(?P<content>.*)$")

        results: list[dict] = []
        for raw_line in out.decode("utf-8", errors="replace").splitlines():
            if len(results) >= limit:
                break
            m = line_re.match(raw_line)
            if not m:
                continue
            results.append(
                {
                    "path": m.group("path"),
                    "line": int(m.group("line")),
                    "snippet": m.group("content").strip()[:200],
                    "score": 0.0, "provider": "ripgrep",
                    "source": "FILE",
                }
            )
        return results
