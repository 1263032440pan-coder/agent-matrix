"""Agent 实体模型（20号规范 §2——Definition/Instance 两层 + ACP 消息）.

Definition: 智能体模板（岗位说明书——静态配置，跨项目，长期存在）
Instance:   实例（照模板造的运行时实体——六态 FSM，项目内）
acp_message: Agent 间/Controller 通信审计（五类消息全归档）
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentSource(str, enum.Enum):
    """Definition 来源（§2.3——AI 生成永不标 BUILT_IN）。"""

    BUILT_IN = "BUILT_IN"                    # 内置五模板（出厂配置）
    USER = "USER"                            # 用户 UI 自建
    CONTROLLER_GENERATED = "CONTROLLER_GENERATED"  # Controller 动态派生


class AgentScope(str, enum.Enum):
    GLOBAL = "GLOBAL"    # 全局模板（跨项目复用）
    PROJECT = "PROJECT"  # 项目模板（AI 生成默认——升 GLOBAL 须用户确认）


class AgentStatus(str, enum.Enum):
    """Instance 六态 FSM（§3）——CREATED→IDLE⇄BUSY→PAUSED/DEGRADED→TERMINATED."""

    CREATED = "CREATED"
    IDLE = "IDLE"
    BUSY = "BUSY"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    TERMINATED = "TERMINATED"


class AgentDefinition(Base):
    """智能体模板——岗位说明书（静态配置，本身不干活）。"""

    __tablename__ = "agent_definition"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_agent_def_id"),
        Index("ix_agent_def_scope", "scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    role_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 模型配置（空=用全局 LLM 配置；禁 <64K 窗口——V1.3 小窗口设防）
    model_config_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 插件/skill 白名单（护栏不是开关——AI 在白名单内自决）
    plugins: Mapped[list | None] = mapped_column(JSON, nullable=True)
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 环节亲和（九标准词子集——路由依据 §6.1；创建表单下拉限定）
    stage_affinity: Mapped[list | None] = mapped_column(JSON, nullable=True)

    source: Mapped[AgentSource] = mapped_column(
        Enum(AgentSource), nullable=False, default=AgentSource.BUILT_IN
    )
    scope: Mapped[AgentScope] = mapped_column(
        Enum(AgentScope), nullable=False, default=AgentScope.GLOBAL
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now
    )


class AgentInstance(Base):
    """智能体实例——运行时状态机（项目内实体）。

    TERMINATED 后记录保留（全部 ACP 消息归档可审计）。
    """

    __tablename__ = "agent_instance"
    __table_args__ = (
        Index("ix_agent_inst_project", "project_id"),
        Index("ix_agent_inst_def", "definition_id"),
        # 项目内同模板可多实例（并发扩容），但同模板同项目只许一个 ACTIVE
        Index(
            "ux_agent_inst_active",
            "project_id",
            "definition_id",
            unique=True,
            sqlite_where=text("status != 'TERMINATED'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    definition_id: Mapped[int] = mapped_column(
        ForeignKey("agent_definition.id"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)

    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), nullable=False, default=AgentStatus.CREATED
    )
    current_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id"), nullable=True
    )

    # §3 DEGRADED 自愈
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # §7 上下文压力（组装器计算副产品——UI 压力条数据源）
    context_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 会话记忆（SessionStore 序列化——完整历史永不删，压缩只是视图）
    session_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


class AcpMessage(Base):
    """ACP 五类消息审计（§5——TERMINATED 后仍可查，可回放）。"""

    __tablename__ = "acp_message"
    __table_args__ = (
        Index("ix_acp_project", "project_id"),
        Index("ix_acp_to_agent", "to_agent_instance_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)

    # 五类: request / inform / correction / report / directive（§5.1）
    msg_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_agent_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_instance.id"), nullable=True
    )
    to_agent_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_instance.id"), nullable=True
    )

    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now
    )
