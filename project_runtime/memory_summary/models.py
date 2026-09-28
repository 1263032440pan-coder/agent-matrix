"""Hierarchical Summary entity (WP-021 / 03-模型).

Scope hierarchy: PROJECT > WORKSTREAM > MODULE > TASK.
Summaries are DERIVED views — never a source of truth (acceptance §4):
  - every summary carries source references (task/artifact/evidence refs)
  - source change marks the summary STALE (acceptance §2)
  - refresh is scoped (only affected summaries, acceptance §3)
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class SummaryScope(str, enum.Enum):
    PROJECT = "PROJECT"
    WORKSTREAM = "WORKSTREAM"
    MODULE = "MODULE"
    TASK = "TASK"


class SummaryStatus(str, enum.Enum):
    FRESH = "FRESH"
    STALE = "STALE"


class ProjectSummary(Base):
    __tablename__ = "project_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # e.g. PROJECT scope_key=""; TASK scope_key="task:42"

    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_decisions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_issues_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source watermark — the version of sources this summary was built from.
    # Any source advancing past this → STALE (acceptance §2).
    source_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        String(8), nullable=False, default=SummaryStatus.STALE.value
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
