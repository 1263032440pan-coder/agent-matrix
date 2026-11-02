"""Runtime 重启后的僵尸 execution 清场（方案 B：RUNNING 判死停住）.

QUEUED → 原样保留，由调用方重新挂 runner。
RUNNING → execution FAILED；关联 task RUNNING→FAILED；释放 BUSY Agent。
不自动 FAILED→READY，FULL_AUTO 心跳不会重提。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.execution.models import Execution
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Task
from project_runtime.worker.agent_models import AgentInstance, AgentStatus
from project_runtime.worker.registry import AgentFSM

logger = logging.getLogger(__name__)

_ACTIVE = ("QUEUED", "RUNNING")


def _status_value(status: object) -> str:
    return getattr(status, "value", status)


async def recover_orphan_executions(session: AsyncSession) -> list[int]:
    """清场 RUNNING orphan；返回需重新 dispatch 的 QUEUED execution id 列表.

    不 commit——由调用方提交。
    """
    zombies = list(
        (
            await session.execute(
                select(Execution).where(Execution.status.in_(_ACTIVE))
            )
        )
        .scalars()
        .all()
    )
    if not zombies:
        return []

    queued_ids: list[int] = []
    now = datetime.now(timezone.utc)

    for z in zombies:
        st = _status_value(z.status)
        if st == "QUEUED":
            queued_ids.append(z.id)
            continue

        # RUNNING → FAILED
        z.status = "FAILED"
        z.error_code = "ORPHAN_RECOVERY"
        z.error_message = "runtime restart orphan"
        z.finished_at = now

        task = await session.get(Task, z.task_id)
        if task is not None and task.status is TaskStatus.RUNNING:
            await StateTransitionService(session).transition_task(
                task, TaskStatus.FAILED, actor="SYSTEM"
            )

        agents = list(
            (
                await session.execute(
                    select(AgentInstance).where(
                        AgentInstance.current_task_id == z.task_id
                    )
                )
            )
            .scalars()
            .all()
        )
        fsm = AgentFSM(session)
        for inst in agents:
            if inst.status is AgentStatus.BUSY:
                await fsm.transition(
                    inst,
                    AgentStatus.IDLE,
                    actor="SYSTEM",
                    reason=f"orphan recovery exec={z.id}",
                )
            inst.current_task_id = None

        logger.info(
            "orphan recovery: exec=%s task=%s → FAILED (agents cleared=%d)",
            z.id,
            z.task_id,
            len(agents),
        )

    await session.flush()
    return queued_ids
