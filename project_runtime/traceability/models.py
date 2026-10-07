"""Traceability entities (WP-022 / 03-模型 追踪链).

七类节点: REQUIREMENT / ARCHITECTURE / TASK / ARTIFACT / TEST / GATE /
DELIVERY。节点不复制实体内容——ref 指向真实实体（id + 可选摘要），
图是投影不是事实源（rebuild_scope 随时可从活数据重建）。
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class TraceKind(str, enum.Enum):
    REQUIREMENT = "REQUIREMENT"
    ARCHITECTURE = "ARCHITECTURE"
    TASK = "TASK"
    ARTIFACT = "ARTIFACT"
    TEST = "TEST"
    GATE = "GATE"
    DELIVERY = "DELIVERY"


class TraceEdgeRel(str, enum.Enum):
    DERIVES = "DERIVES"            # requirement → architecture/task
    IMPLEMENTS = "IMPLEMENTS"      # task/architecture → requirement
    PRODUCES = "PRODUCES"          # task → artifact/test
    VERIFIES = "VERIFIES"          # test → task/requirement
    GATED_BY = "GATED_BY"          # task → gate
    DELIVERS = "DELIVERS"          # artifact → delivery item


class TraceNode(Base):
    __tablename__ = "trace_node"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "ref_id",
                         name="uq_trace_node_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[TraceKind] = mapped_column(Enum(TraceKind), nullable=False)
    # 稳定外部引用（如 "understanding:3#req-0" / "task:42" / "artifact:7"）
    ref_id: Mapped[str] = mapped_column(String(256), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class TraceEdge(Base):
    __tablename__ = "trace_edge"
    __table_args__ = (
        UniqueConstraint("from_node_id", "to_node_id", "rel",
                         name="uq_trace_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    from_node_id: Mapped[int] = mapped_column(
        ForeignKey("trace_node.id"), nullable=False)
    to_node_id: Mapped[int] = mapped_column(
        ForeignKey("trace_node.id"), nullable=False)
    rel: Mapped[TraceEdgeRel] = mapped_column(Enum(TraceEdgeRel), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
