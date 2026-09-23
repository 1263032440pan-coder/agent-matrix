"""Project dashboard API (D17 read-only + minimal write actions for demo)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.project_state.enums import AutomationMode, ProjectStatus
from project_runtime.project_state.models import (
    Artifact,
    GateResult,
    MethodGap,
    Project,
    ProjectReadinessAssessment,
    Task,
    TaskRun,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def get_session():
    async with AsyncSession(engine) as session:
        yield session


class CreateProjectRequest(BaseModel):
    name: str
    automation_mode: str = "FULL_AUTO"


class ModeUpdateRequest(BaseModel):
    automation_mode: str


@router.post("", status_code=201)
async def create_project(
    req: CreateProjectRequest, session: AsyncSession = Depends(get_session)
):
    if req.automation_mode not in ("FULL_AUTO", "MANUAL"):
        raise HTTPException(400, "automation_mode must be FULL_AUTO or MANUAL")
    project = Project(
        name=req.name,
        automation_mode=AutomationMode(req.automation_mode),
        status=ProjectStatus.CREATED,
    )
    session.add(project)
    await session.flush()
    pid, pname = project.id, project.name
    await session.commit()
    return {"id": pid, "name": pname}


@router.patch("/{project_id}/mode", status_code=200)
async def update_project_mode(
    project_id: int, req: ModeUpdateRequest, session: AsyncSession = Depends(get_session)
):
    """Toggle FULL_AUTO / MANUAL at runtime (V0.1: no FSM rule blocks this)."""
    if req.automation_mode not in ("FULL_AUTO", "MANUAL"):
        raise HTTPException(400, "automation_mode must be FULL_AUTO or MANUAL")
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    project.automation_mode = AutomationMode(req.automation_mode)
    pid = project.id
    mode = project.automation_mode.value  # read before commit (attrs expire)
    await session.commit()
    return {"id": pid, "automation_mode": mode}


@router.post("/{project_id}/method-assets/upload", status_code=201)
async def upload_method_asset(
    project_id: int,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    """Import a method asset file (md/txt) uploaded from the UI."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    from project_runtime.method_asset.importer import MethodAssetImporter

    content = await file.read()
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=Path(file.filename or "asset.md").suffix
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        importer = MethodAssetImporter(session)
        asset = await importer.import_file(project_id, tmp_path)
        asset_id = asset.id
        asset_hash = asset.content_hash
        await session.commit()
        return {"id": asset_id, "content_hash": asset_hash}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/{project_id}/execute", status_code=200)
async def execute_project(project_id: int):
    """Dispatch READY tasks via Scheduler + LLMWorker (real LLM execution).

    Dedicated session (isolated from dashboard polling) to avoid SQLite
    lock contention.
    """
    from project_runtime.artifact.manager import ArtifactManager
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.scheduler.scheduler import Scheduler
    from project_runtime.task_graph.manager import TaskGraphManager
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    async def _run(session: AsyncSession):
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")

        artifact_mgr = ArtifactManager(session, workspace_root=f"data/tasks/{project_id}")
        worker = LLMWorkerAdapter(session, artifact_mgr=artifact_mgr)
        scheduler = Scheduler(session, worker=worker, max_workers=1)

        mgr = TaskGraphManager(session)
        tasks = (
            (await session.execute(select(Task).where(Task.project_id == project_id)))
            .scalars()
            .all()
        )
        for t in tasks:
            if t.status == TaskStatus.CREATED:
                try:
                    await mgr.promote_to_ready(t.id)
                except Exception:
                    pass
        await session.commit()  # READY transitions visible before dispatch

        decisions = await scheduler.dispatch_once(project_id)
        await session.commit()
        return decisions

    for attempt in range(2):
        async with AsyncSession(engine) as session:
            try:
                decisions = await _run(session)
                break
            except HTTPException:
                raise
            except Exception:
                await session.rollback()
                if attempt == 1:
                    raise

    manual_pending = sum(1 for d in decisions if d.action == "MANUAL_PENDING")
    return {
        "dispatched": sum(1 for d in decisions if d.action == "DISPATCHED"),
        "manual_pending": manual_pending,
        "hint": (
            "MANUAL 模式：已创建 ManualAction 等待确认；切换到 FULL_AUTO 或确认后执行"
            if manual_pending
            else ""
        ),
        "decisions": [
            {"task_id": d.task_id, "action": d.action, "detail": d.detail}
            for d in decisions
        ],
    }


@router.post("/{project_id}/plan", status_code=200)
async def run_planning(project_id: int):
    """Run D06→D10 chain: normalize → assess → classify → build → plan.

    Uses a dedicated session (separate from dashboard polling sessions) so
    long-running writes don't contend with read polling on the same session.
    """
    from project_runtime.gap.classifier import GapClassifier
    from project_runtime.planner.planner import DynamicPlanner
    from project_runtime.playbook.builder import PlaybookBuilder
    from project_runtime.procedure.normalizer import ProcedureNormalizer
    from project_runtime.readiness.assessor import MethodReadinessAssessor

    async def _run(session: AsyncSession):
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        model = await ProcedureNormalizer.with_llm(session).normalize(project_id)
        assessment = await MethodReadinessAssessor(session).assess(project_id, model.id)
        gaps = await GapClassifier.with_llm(session).classify(assessment.id)
        playbook = await PlaybookBuilder(session).build(project_id)
        tasks = await DynamicPlanner(session).plan(playbook)
        return {
            "model_id": model.id,
            "assessment_id": assessment.id,
            "gaps": len(gaps),
            "playbook_id": playbook.id,
            "tasks": len(tasks),
        }

    for attempt in range(2):
        async with AsyncSession(engine) as session:
            try:
                result = await _run(session)
                await session.commit()
                return result
            except HTTPException:
                raise
            except Exception:
                await session.rollback()
                if attempt == 1:
                    raise


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

    # Tasks (with dependency edges for graph rendering)
    tasks = (
        (await session.execute(select(Task).where(Task.project_id == project_id)))
        .scalars()
        .all()
    )
    from project_runtime.project_state.models import TaskDependency

    dep_rows = (
        (
            await session.execute(
                select(TaskDependency).where(
                    TaskDependency.task_id.in_([t.id for t in tasks] or [0])
                )
            )
        )
        .scalars()
        .all()
    )
    deps_by_task: dict[int, list[int]] = {}
    for d in dep_rows:
        deps_by_task.setdefault(d.task_id, []).append(d.depends_on_id)
    task_list = [
        {
            "id": t.id,
            "name": t.name,
            "status": t.status.value,
            "depends_on": deps_by_task.get(t.id, []),
        }
        for t in tasks
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

    # Execution timeline (TaskRun, newest first)
    runs = (
        (
            await session.execute(
                select(TaskRun, Task.name)
                .join(Task, TaskRun.task_id == Task.id)
                .where(Task.project_id == project_id)
                .order_by(TaskRun.id.desc())
            )
        )
        .all()
    )

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
        "timeline": [
            {
                "id": r.TaskRun.id,
                "task_id": r.TaskRun.task_id,
                "task_name": r.name,
                "attempt": r.TaskRun.attempt,
                "started_at": (
                    r.TaskRun.started_at.isoformat() if r.TaskRun.started_at else None
                ),
                "finished_at": (
                    r.TaskRun.finished_at.isoformat() if r.TaskRun.finished_at else None
                ),
            }
            for r in runs
        ],
    }
