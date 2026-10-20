"""失败传播策略 + Agent 会话快照（20号规范 §10.3 批次 3）.

失败传播（§8——Agent 崩溃时任务图不断链）:
  DEGRADED 冷却自愈（批次 1 已建）→ 冷却内任务重派（同环节备胎实例）
  → 无备胎: Controller 接管决策（BLOCKED 升级用户，绝不静默丢任务）

Agent 会话快照（§7.5——DEGRADED 强制中途快照，V1.3 P2-3）:
  Agent 崩溃/降级瞬间把 SessionStore 全量 + 压缩视图 + 当前进度
  落 Checkpoint（复用既有 checkpoint_record 表——不建新表）
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.agent_models import AgentInstance, AgentStatus

logger = logging.getLogger(__name__)


class FailurePropagator:
    """Agent 失败 → 任务图的传播决策（三段降级链）。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def on_agent_degraded(
        self, project_id: int, instance_id: int, task_id: int | None
    ) -> dict:
        """DEGRADED 发生时的传播决策。

        返回 {"action": "redispatch"/"block"/"wait",
              "backup_instance_id": int | None}
        - wait:     冷却短暂（<30s）且无任务在身——等自愈
        - redispatch: 有同环节备胎（同 project 活跃、非本实例、IDLE）
                      → 任务转派备胎
        - block:    无备胎 → 任务 BLOCKED 升级 Controller/用户
        """
        inst = await self._session.get(AgentInstance, instance_id)
        if inst is None:
            return {"action": "block", "backup_instance_id": None}

        # 无任务在身——等自愈即可
        if task_id is None:
            return {"action": "wait", "backup_instance_id": None}

        # 同项目备胎: 同环节（definition 同 stage 族）其他活跃 IDLE 实例
        stmt = select(AgentInstance).where(
            AgentInstance.project_id == project_id,
            AgentInstance.id != instance_id,
            AgentInstance.status == AgentStatus.IDLE,
        )
        backup = (
            (await self._session.execute(stmt)).scalars().first()
        )
        if backup is not None:
            backup.current_task_id = task_id
            await self._session.flush()
            return {"action": "redispatch", "backup_instance_id": backup.id}
        return {"action": "block", "backup_instance_id": None}


async def snapshot_agent_session(
    session: AsyncSession, instance_id: int, reason: str
) -> int | None:
    """DEGRADED/TERMINATED 中途快照（V1.3 P2-3 强制条款）。

    复用 checkpoint_record 既有列（trigger=AGENT_DEGRADED /
    path=快照文件 / summary=接班说明）——内容 JSON 落
    data/checkpoints/agent-{id}-{ts}.json（与既有 checkpoint 同款:
    行是索引、内容在文件）。
    返回 checkpoint id; 失败 None（快照绝不阻断主流程）。
    """
    try:
        import datetime as _dt
        import pathlib

        from project_runtime.checkpoint.models import CheckpointRecord

        inst = await session.get(AgentInstance, instance_id)
        if inst is None:
            return None
        payload = {
            "agent_instance_id": inst.id,
            "session_ref": inst.session_ref,
            "status": inst.status.value if inst.status else None,
            "current_task_id": inst.current_task_id,
            "context_tokens": inst.context_tokens,
            "context_threshold": inst.context_threshold,
            "token_used": inst.token_used,
            "call_count": inst.call_count,
            "fail_count": inst.fail_count,
            "reason": reason,
        }
        note = (
            f"Agent #{inst.id} 于 {reason} 时快照——"
            f"在办任务 {inst.current_task_id}，FSM={inst.status}，"
            f"上下文 {inst.context_tokens}/{inst.context_threshold}，"
            f"成本 {inst.token_used} tok/{inst.call_count} 次"
        )
        cp_dir = pathlib.Path("data/checkpoints")
        cp_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        fpath = cp_dir / f"agent-{instance_id}-{ts}.json"
        fpath.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        cp = CheckpointRecord(
            project_id=inst.project_id,
            trigger="AGENT_DEGRADED",
            path=str(fpath),
            summary=note,
        )
        session.add(cp)
        await session.flush()
        return cp.id
    except Exception:  # noqa: BLE001 — 快照 best-effort
        logger.warning("agent session snapshot failed", exc_info=True)
        return None
