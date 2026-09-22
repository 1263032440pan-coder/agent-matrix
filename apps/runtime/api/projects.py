"""Read-only project dashboard API (D17)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.project_state.models import (
    Artifact,
    GateResult,
    MethodGap,
    Project,
    ProjectReadinessAssessment,
    Task,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def get_session():
    async with AsyncSession(engine) as session:
        yield session


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Project).order_by(Project.id))
    projects = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "status": p.status.value,
            "automation_mode": p.automation_mode.value,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]


@router.get("/{project_id}/dashboard")
async def project_dashboard(project_id: int, session: AsyncSession = Depends(get_session)):
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Tasks
    tasks = (
        (await session.execute(select(Task).where(Task.project_id == project_id)))
        .scalars()
        .all()
    )
    task_list = [
        {"id": t.id, "name": t.name, "status": t.status.value} for t in tasks
    ]
    current_task = next(
        (t for t in tasks if t.status.value == "RUNNING"), None
    )

    # Artifacts
    artifacts = (
        (
            await session.execute(
                select(Artifact).where(Artifact.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )

    # Gate results
    gates = (
        (
            await session.execute(
                select(GateResult)
                .join(Task, GateResult.task_id == Task.id)
                .where(Task.project_id == project_id)
                .order_by(GateResult.id.desc())
            )
        )
        .scalars()
        .all()
    )

    # Latest readiness
    readiness = (
        (
            await session.execute(
                select(ProjectReadinessAssessment)
                .where(ProjectReadinessAssessment.project_id == project_id)
                .order_by(ProjectReadinessAssessment.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    # Gap count (MethodGap → assessment → project)
    gap_count = (
        await session.execute(
            select(func.count(MethodGap.id))
            .join(
                ProjectReadinessAssessment,
                MethodGap.assessment_id == ProjectReadinessAssessment.id,
            )
            .where(ProjectReadinessAssessment.project_id == project_id)
        )
    ).scalar_one()

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "status": project.status.value,
            "automation_mode": project.automation_mode.value,
        },
        "tasks": task_list,
        "current_task": (
            {"id": current_task.id, "name": current_task.name} if current_task else None
        ),
        "worker_status": {
            "running": sum(1 for t in tasks if t.status.value == "RUNNING"),
            "ready": sum(1 for t in tasks if t.status.value == "READY"),
        },
        "artifacts": [
            {
                "id": a.id,
                "name": a.name,
                "version": a.version,
                "sha256": a.sha256,
                "task_id": a.task_id,
            }
            for a in artifacts
        ],
        "gate_results": [
            {"id": g.id, "task_id": g.task_id, "status": g.status.value}
            for g in gates
        ],
        "readiness": (
            {
                "id": readiness.id,
                "version": readiness.version,
                "coverage_summary": readiness.coverage_summary,
            }
            if readiness
            else None
        ),
        "gap_count": gap_count,
    }
