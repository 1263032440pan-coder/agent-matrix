"""Retrieval Memory Foundation (WP-024 / 08-规范).

Core version: SQLite FTS5 + BM25 + SQL metadata filter + path search +
ripgrep code search. NO Embedding / Rerank / Vector DB (acceptance §9).
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class RetrievalSource(str, enum.Enum):
    ARTIFACT = "ARTIFACT"
    METHOD_ASSET = "METHOD_ASSET"
    SKILL = "SKILL"
    PROCEDURE_NODE = "PROCEDURE_NODE"


class RetrievalDocument(Base):
    """One indexable unit. Versioned + scoped + hash-guarded."""

    __tablename__ = "retrieval_document"
    __table_args__ = (
        # Same content re-indexed → skip (acceptance §7). Partial unique
        # index over ACTIVE rows only — historical (active=0) versions of
        # the same source must not collide with new versions.
        Index(
            "uq_retdoc_same_content",
            "source", "source_id", "content_hash",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
        Index("ix_retdoc_scope_active", "project_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # metadata filter columns (SQL filter — fixed tech)
    doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
