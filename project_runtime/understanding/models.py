"""Project Understanding entities (WP-026 / 10-规范).

Snapshot of what the runtime understood about a project BEFORE method
intelligence runs. Three-way separation is the acceptance core:

  - KnownFacts      grounded in the user's own materials
  - Assumptions     plausible but UNVERIFIED — labelled, never blended
  - OpenQuestions   unknowns that gate downstream decisions

Method Asset Hints point AT user materials (asset ids), they never
materialize a Skill (红线: 不把普通项目文档直接当 Skill).
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class ProjectUnderstanding(Base):
    __tablename__ = "project_understanding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id"), nullable=False, index=True
    )

    # ── hypotheses ──────────────────────────────────────────────────────
    domain_hypothesis: Mapped[str] = mapped_column(
        String(256), nullable=False, default="UNKNOWN"
    )
    project_type_hypothesis: Mapped[str] = mapped_column(
        String(256), nullable=False, default="UNKNOWN"
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── three-way separation (acceptance §1) ────────────────────────────
    known_facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_questions_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # requirements surfaced by understanding (not yet normalized)
    known_requirements_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_gaps_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── method asset hints (acceptance §2) ──────────────────────────────
    # [{"asset_id": 3, "signal": "SOP", "provenance": "user-material",
    #   "confidence": 0.9, "rationale": "含分阶段流程+验收标准"}]
    # Points at the asset — NEVER registers a Skill (acceptance §3).
    method_asset_hints_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    analyzer_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v0.1"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
