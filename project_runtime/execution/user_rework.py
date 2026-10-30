"""SYS-143～146: 单节点用户 Rework（确认框 → Core → FSM）。

用户出口与 Gate 心跳分离：确认后目标立即 REWORK→READY；
连带 HARD 下游先挂 REWORK，上游再次 ACCEPTED 后以
actor=user_rework_release 释放。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.event.models import RuntimeEvent, RuntimeEventType
from project_runtime.project_state.enums import (
    AutomationMode,
    DependencyType,
    TaskStatus,
)
from project_runtime.project_state.fsm import (
    InvalidStateTransitionError,
    StateTransitionService,
)
from project_runtime.project_state.models import Project, Task, TaskDependency
from project_runtime.task_graph.manager import TaskGraphManager

logger = logging.getLogger(__name__)

ACTOR_USER = "user"
ACTOR_RELEASE = "user_rework_release"

REOPEN_STATUSES = frozenset({
    TaskStatus.ACCEPTED,
    TaskStatus.FAILED,
    TaskStatus.BLOCKED,
    TaskStatus.REWORK,
})


class UserReworkNotFoundError(LookupError):
    """项目或任务不存在 / 不属于该项目。"""


class UserReworkConflictError(ValueError):
    """目标不可重开或依赖未齐。"""


def _status_val(s: TaskStatus | str) -> TaskStatus:
    return s if isinstance(s, TaskStatus) else TaskStatus(s)


async def _get_project_task(
    session: AsyncSession, project_id: int, task_id: int,
) -> tuple[Project, Task]:
    proj = await session.get(Project, project_id)
    if proj is None:
        raise UserReworkNotFoundError(f"project {project_id}")
    task = await session.get(Task, task_id)
    if task is None or task.project_id != project_id:
        raise UserReworkNotFoundError(f"task {task_id}")
    return proj, task


async def _direct_hard_downstream(
    session: AsyncSession, task_id: int,
) -> list[Task]:
    rows = (
        await session.execute(
            select(TaskDependency).where(
                TaskDependency.depends_on_id == task_id,
                TaskDependency.dependency_type == DependencyType.HARD,
            )
        )
    ).scalars().all()
    out: list[Task] = []
    for dep in rows:
        t = await session.get(Task, dep.task_id)
        if t is not None:
            out.append(t)
    return out


async def _compute_cascade(
    session: AsyncSession,
    target: Task,
    *,
    cascade_hard_downstream: bool,
) -> tuple[list[int], list[dict[str, Any]]]:
    cascaded: list[int] = []
    skipped: list[dict[str, Any]] = []
    if not cascade_hard_downstream:
        return cascaded, skipped
    for down in await _direct_hard_downstream(session, target.id):
        st = _status_val(down.status)
        if st in REOPEN_STATUSES:
            cascaded.append(down.id)
        else:
            skipped.append({
                "task_id": down.id,
                "status": st.value,
                "reason": "NOT_REOPEN_STATUS",
            })
    return cascaded, skipped


async def preview_user_rework(
    session: AsyncSession,
    project_id: int,
    task_id: int,
    *,
    cascade_hard_downstream: bool = False,
) -> dict[str, Any]:
    """dry_run：只计算，不写状态。"""
    _proj, task = await _get_project_task(session, project_id, task_id)
    st = _status_val(task.status)
    mgr = TaskGraphManager(session)
    can = await mgr.can_set_ready(task.id)
    if st not in REOPEN_STATUSES:
        raise UserReworkConflictError(
            f"task {task_id} status {st.value} not reopenable"
        )
    if not can and st != TaskStatus.REWORK:
        # REWORK 且依赖未齐：预览仍可说明将 409；点确认时硬拦
        raise UserReworkConflictError(
            f"task {task_id} HARD upstream not all ACCEPTED"
        )
    if st == TaskStatus.REWORK and not can:
        raise UserReworkConflictError(
            f"task {task_id} already REWORK but HARD upstream not ready"
        )
    cascaded, skipped = await _compute_cascade(
        session, task, cascade_hard_downstream=cascade_hard_downstream,
    )
    return {
        "task_id": task.id,
        "from_status": st.value,
        "to_status": "READY",
        "released": True,
        "cascaded_task_ids": cascaded,
        "skipped_downstream": skipped,
    }


def _emit_user_rework_event(
    session: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    message: str,
    payload: dict[str, Any],
    actor: str,
) -> None:
    session.add(
        RuntimeEvent(
            event_type=RuntimeEventType.USER_TASK_REWORK.value,
            project_id=project_id,
            task_id=task_id,
            phase="USER_REWORK",
            actor=actor,
            message=message[:120],
            payload=json.dumps(payload, ensure_ascii=False),
        )
    )


async def _to_rework_then_ready(
    session: AsyncSession,
    task: Task,
    *,
    actor: str,
) -> str:
    """目标：非 REWORK → REWORK → READY；已是 REWORK → READY。"""
    fsm = StateTransitionService(session)
    st = _status_val(task.status)
    from_status = st.value
    if st != TaskStatus.REWORK:
        await fsm.transition_task(task, TaskStatus.REWORK, actor=actor)
    await fsm.transition_task(task, TaskStatus.READY, actor=actor)
    return from_status


async def run_user_rework(
    session: AsyncSession,
    project_id: int,
    task_id: int,
    *,
    cascade_hard_downstream: bool = False,
    submit: bool = True,
) -> dict[str, Any]:
    """确认提交：写状态 + 事件；可选 commit 后 submit（由调用方控制）。

    本函数内不 commit；调用方负责 commit，再按返回值 submit。
    ``submit`` 参数仅表示「调用方是否计划提交」——实际 submit 在 API 层。
    """
    del submit  # API 层处理；保留签名对齐方案
    proj, task = await _get_project_task(session, project_id, task_id)
    st = _status_val(task.status)
    mgr = TaskGraphManager(session)
    if st not in REOPEN_STATUSES:
        raise UserReworkConflictError(
            f"task {task_id} status {st.value} not reopenable"
        )
    if not await mgr.can_set_ready(task.id):
        raise UserReworkConflictError(
            f"task {task_id} HARD upstream not all ACCEPTED"
        )

    cascaded, skipped = await _compute_cascade(
        session, task, cascade_hard_downstream=cascade_hard_downstream,
    )

    try:
        from_status = await _to_rework_then_ready(
            session, task, actor=ACTOR_USER,
        )
        fsm = StateTransitionService(session)
        for did in cascaded:
            down = await session.get(Task, did)
            if down is None:
                continue
            dst = _status_val(down.status)
            if dst == TaskStatus.REWORK:
                continue
            await fsm.transition_task(down, TaskStatus.REWORK, actor=ACTOR_USER)
    except InvalidStateTransitionError as e:
        raise UserReworkConflictError(str(e)) from e

    payload = {
        "from_status": from_status,
        "to_status": "READY",
        "cascade_hard_downstream": cascade_hard_downstream,
        "cascaded_task_ids": cascaded,
        "skipped_downstream": skipped,
        "released": True,
        "bypass_gate_fuse": True,
    }
    _emit_user_rework_event(
        session,
        project_id=project_id,
        task_id=task.id,
        message=f"user-rework → READY cascade={len(cascaded)}",
        payload=payload,
        actor=ACTOR_USER,
    )
    await session.flush()

    return {
        "task_id": task.id,
        "from_status": from_status,
        "to_status": "READY",
        "released": True,
        "cascaded_task_ids": cascaded,
        "skipped_downstream": skipped,
        "automation_mode": (
            proj.automation_mode.value
            if hasattr(proj.automation_mode, "value")
            else str(proj.automation_mode)
        ),
        "should_submit": proj.automation_mode == AutomationMode.FULL_AUTO,
    }


async def _latest_cascade_ids(
    session: AsyncSession, project_id: int, upstream_task_id: int,
) -> list[int]:
    rows = (
        await session.execute(
            select(RuntimeEvent)
            .where(
                RuntimeEvent.project_id == project_id,
                RuntimeEvent.task_id == upstream_task_id,
                RuntimeEvent.event_type
                == RuntimeEventType.USER_TASK_REWORK.value,
            )
            .order_by(RuntimeEvent.id.desc())
            .limit(20)
        )
    ).scalars().all()
    for ev in rows:
        try:
            payload = json.loads(ev.payload or "{}")
        except (TypeError, ValueError):
            continue
        # 点确认时的事件：有 cascaded 列表且无 released_by_upstream
        if payload.get("released_by_upstream"):
            continue
        ids = payload.get("cascaded_task_ids")
        if isinstance(ids, list):
            return [int(x) for x in ids]
    return []


async def release_cascaded_after_accept(
    session: AsyncSession,
    project_id: int,
    upstream_task_id: int,
    *,
    submit: bool = True,
) -> list[int]:
    """上游 Gate ACCEPTED 后：以 user_rework_release 释放本次连带下游。"""
    del submit
    proj = await session.get(Project, project_id)
    if proj is None:
        return []
    upstream = await session.get(Task, upstream_task_id)
    if upstream is None or _status_val(upstream.status) != TaskStatus.ACCEPTED:
        return []

    cascaded_ids = await _latest_cascade_ids(
        session, project_id, upstream_task_id,
    )
    if not cascaded_ids:
        return []

    mgr = TaskGraphManager(session)
    fsm = StateTransitionService(session)
    released: list[int] = []
    for did in cascaded_ids:
        down = await session.get(Task, did)
        if down is None:
            continue
        if _status_val(down.status) != TaskStatus.REWORK:
            continue
        if not await mgr.can_set_ready(down.id):
            continue
        try:
            await fsm.transition_task(
                down, TaskStatus.READY, actor=ACTOR_RELEASE,
            )
        except InvalidStateTransitionError:
            logger.debug(
                "release cascaded skip task=%s", did, exc_info=True,
            )
            continue
        _emit_user_rework_event(
            session,
            project_id=project_id,
            task_id=down.id,
            message=f"release after upstream {upstream_task_id} ACCEPTED",
            payload={
                "from_status": "REWORK",
                "to_status": "READY",
                "released": True,
                "bypass_gate_fuse": True,
                "released_by_upstream": upstream_task_id,
                "actor": ACTOR_RELEASE,
            },
            actor=ACTOR_RELEASE,
        )
        released.append(down.id)
    await session.flush()
    return released
