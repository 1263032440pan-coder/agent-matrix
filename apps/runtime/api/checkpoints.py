"""Checkpoint API (WP-017 接线收尾批 P2-2).

POST /api/projects/{id}/checkpoints          创建快照（trigger=MANUAL）
POST /api/checkpoints/{id}/restore           恢复到新副本
GET  /api/projects/{id}/checkpoints          列出项目全部快照
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.checkpoint import CheckpointError, CheckpointManager
from project_runtime.checkpoint.models import CheckpointRecord

router = APIRouter(prefix="/api", tags=["checkpoints"])


class CreateCheckpointResponse(BaseModel):
    checkpoint_id: int
    trigger: str
    path: str
    summary: str


@router.post("/projects/{project_id}/checkpoints", status_code=201)
async def create_checkpoint(project_id: int):
    async with AsyncSession(engine) as session:
        try:
            mgr = CheckpointManager(session)
            record = await mgr.create(project_id, trigger="MANUAL")
            out = {
                "checkpoint_id": record.id,
                "trigger": record.trigger,
                "path": record.path,
                "summary": record.summary or "",
            }  # snapshot before commit
            await session.commit()
        except CheckpointError as e:
            raise HTTPException(404, str(e)) from e
        return out


@router.get("/projects/{project_id}/checkpoints", status_code=200)
async def list_checkpoints(project_id: int):
    async with AsyncSession(engine) as session:
        stmt = (
            select(CheckpointRecord)
            .where(CheckpointRecord.project_id == project_id)
            .order_by(CheckpointRecord.id.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id,
                "trigger": r.trigger,
                "schema_version": r.schema_version,
                "summary": r.summary,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.post("/checkpoints/{checkpoint_id}/restore", status_code=201)
async def restore_checkpoint(checkpoint_id: int):
    async with AsyncSession(engine) as session:
        try:
            mgr = CheckpointManager(session)
            new_pid = await mgr.restore(checkpoint_id)
            await session.commit()
        except CheckpointError as e:
            raise HTTPException(404, str(e)) from e
        return {"restored_project_id": new_pid}
