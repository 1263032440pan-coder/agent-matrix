"""Checkpoint entities (WP-017).

快照式 Checkpoint: 显式 JSON 导出（不复制 DB 文件）——恢复语义清晰、
内容可审计、跨版本可校验。恢复到新项目副本（原项目永不覆盖——安全
恢复语义）。
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class CheckpointRecord(Base):
    """一次 checkpoint 的元数据（内容在 JSON 文件，行是索引）。"""

    __tablename__ = "checkpoint_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False,
                                         default="MANUAL")
    # MANUAL / AUTO_GATE_FAIL / AUTO_MILESTONE / AUTO_PERIODIC
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False,
                                                default="v1")
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
