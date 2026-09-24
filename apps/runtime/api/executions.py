"""Execution API (WP-040): POST /api/executions (202) + GET /api/executions/{id}."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.execution.models import Execution
from project_runtime.execution.repository import (
    ActiveExecutionExistsError,
    ExecutionRepository,
)
from project_runtime.execution.service import ExecutionService
from project_runtime.project_state.models import Project, Task

router = APIRouter(prefix="/api/executions", tags=["executions"])

_service = ExecutionService()


@router.get("/diagnostics/locks")
async def lock_diagnostics():
    """RH-02 衔接: expose commit_with_retry lock audit (DB-06 evidence).

    In-process counters since startup; consumed by ops/diagnostics, not by
    the normal execution path.
    """
    from infrastructure.database.short_tx import get_lock_audit

    events = get_lock_audit()
    return {"lock_retry_count": len(events), "events": events[-50:]}


class CreateExecutionRequest(BaseModel):
    project_id: int
    task_id: int


def _serialize(ex: Execution) -> dict:
    return {
        "execution_id": ex.id,
        "project_id": ex.project_id,
        "task_id": ex.task_id,
        "attempt_no": ex.attempt_no,
        "status": ex.status,
        "phase": ex.phase,
        "message": ex.message,
        "skill_id": ex.skill_id,
        "skill_version": ex.skill_version,
        "error_code": ex.error_code,
        "error_message": ex.error_message,
        "created_at": ex.created_at.isoformat() if ex.created_at else None,
        "started_at": ex.started_at.isoformat() if ex.started_at else None,
        "updated_at": ex.updated_at.isoformat() if ex.updated_at else None,
        "finished_at": ex.finished_at.isoformat() if ex.finished_at else None,
    }


@router.post("", status_code=202)
async def create_execution(req: CreateExecutionRequest):
    """Admission only: create QUEUED Execution, commit, return 202 (AE-01/02).

    Background runner continues beyond the HTTP lifecycle (AE-03).
    """
    async with AsyncSession(engine) as session:
        project = await session.get(Project, req.project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        task = await session.get(Task, req.task_id)
        if task is None or task.project_id != req.project_id:
            raise HTTPException(404, "Task not found in project")

    try:
        execution_id = await _service.submit(req.project_id, req.task_id)
    except ActiveExecutionExistsError as e:
        # AE-05: same Task duplicate start → no second active Execution
        raise HTTPException(409, str(e)) from e

    return {"execution_id": execution_id, "task_id": req.task_id, "status": "QUEUED"}


@router.get("/{execution_id}")
async def get_execution(execution_id: int):
    """Query Execution state. Persisted — survives process restart (AE-04)."""
    async with AsyncSession(engine) as session:
        repo = ExecutionRepository(session)
        ex = await repo.get(execution_id)
        if ex is None:
            raise HTTPException(404, "Execution not found")
        return _serialize(ex)


def _serialize_event(ev) -> dict:
    return {
        "event_id": ev.id,
        "event_type": ev.event_type,
        "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
        "project_id": ev.project_id,
        "task_id": ev.task_id,
        "execution_id": ev.execution_id,
        "phase": ev.phase,
        "message": ev.message,
        "actor": ev.actor,
        "payload": ev.payload,
    }


@router.get("/{execution_id}/events")
async def list_execution_events(execution_id: int, after_id: int = 0, limit: int = 200):
    """Event history for an execution (spec §8 after_id reconnection recovery)."""
    from project_runtime.event import EventEngine

    async with AsyncSession(engine) as session:
        if await session.get(Execution, execution_id) is None:
            raise HTTPException(404, "Execution not found")

    events = await EventEngine().list_for_execution(
        execution_id, after_id=after_id, limit=limit
    )
    return {
        "execution_id": execution_id,
        "events": [_serialize_event(e) for e in events],
        "last_event_id": events[-1].id if events else after_id,
    }


@router.get("/{execution_id}/events/stream")
async def stream_execution_events(execution_id: int, after_id: int = 0):
    """SSE live event stream (WP-042).

    Polls the Event Store and pushes new events. Subscriber disconnects are
    silently ignored — Worker never depends on SSE health (acceptance §4).
    """
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    from project_runtime.event import EventEngine

    engine_ev = EventEngine()

    async def event_gen():
        cursor = after_id
        idle_ticks = 0
        while True:
            events = await engine_ev.list_for_execution(execution_id, after_id=cursor)
            for ev in events:
                cursor = ev.id
                data = _json.dumps(_serialize_event(ev), ensure_ascii=False)
                yield f"id: {ev.id}\nevent: {ev.event_type}\ndata: {data}\n\n"
                idle_ticks = 0
            if not events:
                idle_ticks += 1
                if idle_ticks % 5 == 0:  # ~2.5s heartbeat comment keeps proxies open
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{execution_id}/retry", status_code=202)
async def retry_execution(execution_id: int):
    """Retry a FAILED execution: FAILED task → READY (FSM-legal, fsm.py:31),
    then submit a NEW execution (attempt_no increments — AE-06).

    Also handles stranded EXECUTION_DONE tasks whose gate never ran.
    """
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.project_state.fsm import StateTransitionService
    from project_runtime.task_graph.manager import TaskGraphManager

    async with AsyncSession(engine) as session:
        ex = await session.get(Execution, execution_id)
        if ex is None:
            raise HTTPException(404, "Execution not found")
        if ex.status != "FAILED":
            raise HTTPException(409, f"Only FAILED executions can retry (got {ex.status})")
        task_id, project_id = ex.task_id, ex.project_id

        mgr = TaskGraphManager(session)
        task = await mgr.get_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        if task.status == TaskStatus.FAILED:
            # FSM-legal reset: FAILED → READY (explicit retry intent)
            await StateTransitionService(session).transition_task(
                task, TaskStatus.READY
            )
            await session.commit()
        # If task is READY (or reset above), submit new attempt
        task2 = await mgr.get_task(task_id)
        task_status = task2.status
        await session.commit()

    if task_status != TaskStatus.READY:
        raise HTTPException(
            409, f"Task in {task_status.value} cannot retry — needs manual handling"
        )

    try:
        new_id = await _service.submit(project_id, task_id)
    except ActiveExecutionExistsError as e:
        raise HTTPException(409, str(e)) from e
    return {
        "execution_id": new_id,
        "task_id": task_id,
        "attempt_of": execution_id,
        "status": "QUEUED",
    }
