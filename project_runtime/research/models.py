"""Research Manager entities (WP-029 / 10-规范 Research-on-Gap).

ResearchRecord 是可复用的研究资产——创建前必须先查既有（五查），
命中即引用不重建。研究不覆盖用户 MANDATORY 流程（红线）。
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class ResearchRecord(Base):
    __tablename__ = "research_record"
    __table_args__ = (
        # dedupe: same question + research kind is ONE record (updated, not
        # duplicated) — the "Existing Research" check materialized as a
        # uniqueness constraint
        UniqueConstraint("question", "research_kind", name="uq_research_q_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    research_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # DOMAIN_RESEARCH / TECHNICAL_RESEARCH / PROJECT_METHOD_RESEARCH

    question: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"source": "...", "content": "...", "confidence": 0.8}]

    gap_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    # OPEN / ANSWERED / SUPERSEDED

    source_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
