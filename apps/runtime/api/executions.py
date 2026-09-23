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
