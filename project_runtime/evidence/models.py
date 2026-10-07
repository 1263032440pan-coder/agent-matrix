"""Completion Evidence entities (WP-023 / ADR-010).

八类型 Evidence 关联真实实体（ref 指向 + 摘要），valid 位支持失效
与重建。完成判定基于证据链——禁止仅通过 LLM 语义判断（ADR-010）。
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class EvidenceType(str, enum.Enum):
    TASK_ACCEPTED = "TASK_ACCEPTED"
    GATE_PASSED = "GATE_PASSED"
    TEST_PASSED = "TEST_PASSED"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    CONTRACT_ITEM_SATISFIED = "CONTRACT_ITEM_SATISFIED"
    MANUAL_ACTION_CONFIRMED = "MANUAL_ACTION_CONFIRMED"
    INTEGRATION_PASSED = "INTEGRATION_PASSED"
    MANUAL_EVIDENCE = "MANUAL_EVIDENCE"


class EvidenceScope(str, enum.Enum):
    TASK = "TASK"
    MODULE = "MODULE"
    DELIVERY_ITEM = "DELIVERY_ITEM"
    PROJECT = "PROJECT"


class CompletionEvidence(Base):
    __tablename__ = "completion_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(EvidenceType), nullable=False
    )
    scope: Mapped[EvidenceScope] = mapped_column(
        Enum(EvidenceScope), nullable=False, default=EvidenceScope.TASK
    )
    # 关联实体引用（如 "task:42" / "gate:7" / "artifact:3" /
    # "test:artifact:5" / "delivery_item:9" / "manual_action:4"）
    ref: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    invalidated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    invalidated_reason: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
