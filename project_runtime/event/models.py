"""Runtime Event domain (WP-042 / ADR-043 / 22-Event-Engine与实时进度规范).

State answers "what is it doing now"; Event answers "what happened before".
Both must exist — Event is the audit/progress trail keyed by execution_id.

Minimal event set (spec §3):
  EXECUTION_QUEUED / EXECUTION_STARTED / EXECUTION_COMPLETED /
  EXECUTION_FAILED / EXECUTION_CANCELLED
  SKILL_RESOLUTION_STARTED / SKILL_RESOLVED / SKILL_LOADED
  WORKER_STARTED / WORKER_COMPLETED
  ARTIFACT_CREATED
  GATE_STARTED / GATE_FAILED / GATE_PASSED

Writes use short transactions (spec §9) — never a long tx around event insert.
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

# Register the Execution model in the shared metadata — RuntimeEvent's FK
# ("execution.id") can only resolve when the target table is defined.
from project_runtime.execution.models import Execution  # noqa: F401, I001
from project_runtime.project_state.base import Base


class RuntimeEventType(str, enum.Enum):
    EXECUTION_QUEUED = "EXECUTION_QUEUED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"
    SKILL_RESOLUTION_STARTED = "SKILL_RESOLUTION_STARTED"
    SKILL_RESOLVED = "SKILL_RESOLVED"
    SKILL_LOADED = "SKILL_LOADED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_FAILED = "REVIEW_FAILED"
    REVIEW_PASSED = "REVIEW_PASSED"
    REPAIR_STARTED = "REPAIR_STARTED"
    REPAIR_COMPLETED = "REPAIR_COMPLETED"
    GATE_STARTED = "GATE_STARTED"
    GATE_FAILED = "GATE_FAILED"
    GATE_PASSED = "GATE_PASSED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    LLM_THINKING_CHUNK = "LLM_THINKING_CHUNK"   # 思维链流式增量（WP: CoT UI）
    LLM_ANSWER_CHUNK = "LLM_ANSWER_CHUNK"       # 正式答案流式增量
    LLM_TOOL_CALL = "LLM_TOOL_CALL"             # 模型发起工具调用（规范 19 §4.3）
    LLM_TOOL_RESULT = "LLM_TOOL_RESULT"         # 工具执行完成（审计: ok/计数/时长）
    AGENT_CREATED = "AGENT_CREATED"             # Agent 实例创建（规范 20 §3.3）
    AGENT_IDLE = "AGENT_IDLE"
    AGENT_BUSY = "AGENT_BUSY"
    AGENT_PAUSED = "AGENT_PAUSED"
    AGENT_DEGRADED = "AGENT_DEGRADED"
    AGENT_RECOVERED = "AGENT_RECOVERED"
    AGENT_TERMINATED = "AGENT_TERMINATED"
    AGENT_CONTEXT_COMPRESSED = "AGENT_CONTEXT_COMPRESSED"  # 会话压缩发生（§7.3 审计）
    MANIFEST_CHECK = "MANIFEST_CHECK"               # Controller 清单对账（规范 20 §3.4）
    RECONCILIATION_GAP = "RECONCILIATION_GAP"       # V1.6 序5: 收口对账缺件


class RuntimeEvent(Base):
    __tablename__ = "runtime_event"

    # NOTE: Integer (not BigInteger) — SQLite autoincrement requires
    # INTEGER PRIMARY KEY; BigInteger maps to BIGINT and breaks rowid aliasing.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("project.id"), nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("task.id"), nullable=True
    )
    execution_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("execution.id"), nullable=True
    )

    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
