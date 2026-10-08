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


@router.post("/{project_id}/understanding", status_code=201)
async def analyze_understanding(project_id: int):
    """WP-026: run Project Understanding → persisted snapshot.

    Planning Context consumption point — downstream (WP-030 Playbook)
    reads the snapshot via GET. Deterministic analyzer in V0.1; the
    UnderstandingAnalyzer protocol is the WP-037 LLM swap point.
    """
    from project_runtime.understanding import UnderstandingService

    async with AsyncSession(engine) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        goal = project.goal or ""
        svc = UnderstandingService(session)
        row = await svc.analyze_project(project_id)
        # snapshot before commit (ORM expiry)
        out = {
            "id": row.id,
            "project_id": row.project_id,
            "domain_hypothesis": row.domain_hypothesis,
            "project_type_hypothesis": row.project_type_hypothesis,
            "goal": row.goal or goal,
            "known_facts": row.known_facts_json,
            "assumptions": row.assumptions_json,
            "open_questions": row.open_questions_json,
            "known_requirements": row.known_requirements_json,
            "knowledge_gaps": row.knowledge_gaps_json,
            "method_asset_hints": row.method_asset_hints_json,
            "analyzer_version": row.analyzer_version,
        }
        await session.commit()
    return out


@router.get("/{project_id}/understanding", status_code=200)
async def get_understanding(project_id: int):
    """Latest understanding snapshot for a project (Planning Context read)."""
    from project_runtime.understanding.models import ProjectUnderstanding

    async with AsyncSession(engine) as session:
        stmt = (
            select(ProjectUnderstanding)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "No understanding snapshot yet")
        return {
            "id": row.id,
            "project_id": row.project_id,
            "domain_hypothesis": row.domain_hypothesis,
            "project_type_hypothesis": row.project_type_hypothesis,
            "goal": row.goal,
            "known_facts": row.known_facts_json,
            "assumptions": row.assumptions_json,
            "open_questions": row.open_questions_json,
            "known_requirements": row.known_requirements_json,
            "knowledge_gaps": row.knowledge_gaps_json,
            "method_asset_hints": row.method_asset_hints_json,
            "analyzer_version": row.analyzer_version,
        }


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


@router.post("/{project_id}/execute", status_code=202)
async def execute_project(project_id: int):
    """V1.13 (CR-003 方案 A): thin wrapper over the async Execution channel.

    Promotes CREATED→READY, then admits one QUEUED Execution per READY task
    (MANUAL mode still yields ManualAction, not Executions — mode red line).
    Returns 202 immediately; execution continues in background.
    """
    from project_runtime.execution.repository import (
        ActiveExecutionExistsError,
    )
    from project_runtime.execution.service import ExecutionService
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.task_graph.manager import TaskGraphManager

    async with AsyncSession(engine) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        # Snapshot mode BEFORE the promote loop's commit — ORM objects expire
        # on commit and attribute access afterwards triggers sync IO (the
        # MissingGreenlet 500 bug seen in production on 2026-09-24).
        is_manual = project.automation_mode == AutomationMode.MANUAL

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
        await session.commit()

        ready_ids = [
            t.id
            for t in (
                await session.execute(select(Task).where(Task.project_id == project_id))
            ).scalars().all()
            if t.status == TaskStatus.READY
        ]

    if is_manual:
        return {
            "executions": [],
            "manual_pending": len(ready_ids),
            "hint": "MANUAL 模式：不自动创建 Execution；请切换 FULL_AUTO 或走 ManualAction 确认",
        }

    service = ExecutionService()
    created: list[int] = []
    skipped_active = 0
    for task_id in ready_ids:
        try:
            ex_id = await service.submit(project_id, task_id)
            created.append(ex_id)
        except ActiveExecutionExistsError:
            skipped_active += 1  # AE-05 idempotency: already active

    return {
        "executions": created,
        "skipped_active": skipped_active,
        "manual_pending": 0,
        "hint": "",
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
            except Exception as e:
                await session.rollback()
                if attempt == 1:
                    # LLM-configured extraction failure must surface clearly,
                    # never silently degrade (user decision 2026-09-25).
                    raise HTTPException(
                        502,
                        f"规划失败：LLM 抽取未成功（已配置大模型，禁止降级）。"
                        f"请检查 LLM 配置/网关后重试。原因：{str(e)[:200]}",
                    ) from e


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


@router.post("/manual-actions/{action_id}/confirm", status_code=200)
async def confirm_manual_action(action_id: int):
    """WP-018: MANUAL 模式人工确认 → CONFIRMED + 提交异步执行。"""
    from project_runtime.execution.service import ExecutionService
    from project_runtime.project_state.enums import ManualActionStatus
    from project_runtime.project_state.models import ManualAction

    async with AsyncSession(engine) as session:
        action = await session.get(ManualAction, action_id)
        if action is None:
            raise HTTPException(404, "ManualAction not found")
        if action.status != ManualActionStatus.PENDING:
            raise HTTPException(409, f"ManualAction {action_id} not PENDING")
        action.status = ManualActionStatus.CONFIRMED
        task_id, project_id = action.task_id, action.project_id
        await session.commit()

    service = ExecutionService()
    ex_id = await service.submit(project_id, task_id)
    return {"status": "CONFIRMED", "execution_id": ex_id}


@router.post("/manual-actions/{action_id}/reject", status_code=200)
async def reject_manual_action(action_id: int):
    """WP-018: MANUAL 模式人工拒绝 → REJECTED（任务留 READY，可再排队）。"""
    from project_runtime.project_state.enums import ManualActionStatus
    from project_runtime.project_state.models import ManualAction

    async with AsyncSession(engine) as session:
        action = await session.get(ManualAction, action_id)
        if action is None:
            raise HTTPException(404, "ManualAction not found")
        if action.status != ManualActionStatus.PENDING:
            raise HTTPException(409, f"ManualAction {action_id} not PENDING")
        action.status = ManualActionStatus.REJECTED
        await session.commit()
    return {"status": "REJECTED"}


@router.delete("/{project_id}", status_code=200)
async def delete_project(project_id: int):
    """级联删除项目全部数据（26 直连表 + 7 间接表 + 磁盘产物/快照）。"""
    from project_runtime.project_state.deletion import (
        ProjectDeletionError,
        ProjectDeletionService,
    )

    async with AsyncSession(engine) as session:
        svc = ProjectDeletionService(session)
        try:
            result = await svc.delete(project_id)
        except ProjectDeletionError as e:
            raise HTTPException(404, str(e)) from e
    return result


@router.post("/batch-delete", status_code=200)
async def batch_delete_projects(req: dict):
    """批量级联删除。body: {"project_ids": [1,2,3]}"""
    from project_runtime.project_state.deletion import (
        ProjectDeletionError,
        ProjectDeletionService,
    )

    ids = req.get("project_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "project_ids must be a list of integers")
    if not ids:
        raise HTTPException(400, "project_ids is empty")

    results = []
    async with AsyncSession(engine) as session:
        svc = ProjectDeletionService(session)
        for pid in ids:
            try:
                r = await svc.delete(pid)
                results.append(r)
            except ProjectDeletionError as e:
                results.append({"project_id": pid, "error": str(e)})
    return {"deleted": results}


@router.get("/{project_id}/summary")
async def project_summary(project_id: int):
    """WP-018: 轻量项目摘要（历史列表用——不拉 dashboard 全量）。"""
    from sqlalchemy import func as _func

    async with AsyncSession(engine) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        stmt = select(Task.status, _func.count(Task.id)).where(
            Task.project_id == project_id
        ).group_by(Task.status)
        by_status = dict(
            (await session.execute(stmt)).all()
        )
    return {
        "task_total": sum(by_status.values()),
        "task_accepted": by_status.get("ACCEPTED", 0),
        "task_running": by_status.get("RUNNING", 0),
    }


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
        # WP-042: recent runtime events for this project (live progress trail)
        "runtime_events": await _recent_events(session, project_id),
        # WP-018: manual actions (MANUAL mode pending confirmations)
        "manual_actions": await _manual_actions(session, project_id),
        # WP-018: delivery contract view
        "delivery": await _delivery_view(session, project_id),
        # WP-018: current frontier (READY task ids — 可推进边界高亮)
        "frontier": [t.id for t in tasks if t.status.value == "READY"],
    }


async def _manual_actions(session: AsyncSession, project_id: int) -> list[dict]:
    """PENDING ManualActions for a project (MANUAL mode queue)."""
    from project_runtime.project_state.enums import ManualActionStatus
    from project_runtime.project_state.models import ManualAction

    stmt = select(ManualAction).where(
        ManualAction.project_id == project_id,
        ManualAction.status == ManualActionStatus.PENDING,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "task_id": m.task_id,
            "action_type": m.action_type.value,
            "status": m.status.value,
        }
        for m in rows
    ]


async def _delivery_view(session: AsyncSession, project_id: int) -> dict | None:
    """Latest delivery contract + items for a project."""
    from project_runtime.project_state.models import (
        DeliveryContract,
        DeliveryItem,
    )

    stmt = (
        select(DeliveryContract)
        .where(DeliveryContract.project_id == project_id)
        .order_by(DeliveryContract.id.desc())
        .limit(1)
    )
    contract = (await session.execute(stmt)).scalar_one_or_none()
    if contract is None:
        return None
    items = (
        (
            await session.execute(
                select(DeliveryItem).where(
                    DeliveryItem.contract_id == contract.id
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "contract_id": contract.id,
        "version": contract.version,
        "status": contract.status.value,
        "items": [
            {
                "id": i.id,
                "requirement_ref": i.requirement_ref,
                "status": i.status.value,
            }
            for i in items
        ],
    }


async def _recent_events(session: AsyncSession, project_id: int) -> list[dict]:
    """Latest 30 runtime events for a project (newest first)."""
    from project_runtime.event.models import RuntimeEvent

    stmt = (
        select(RuntimeEvent)
        .where(RuntimeEvent.project_id == project_id)
        .order_by(RuntimeEvent.id.desc())
        .limit(30)
    )
    events = list((await session.execute(stmt)).scalars().all())
    return [
        {
            "event_id": e.id,
            "event_type": e.event_type,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "execution_id": e.execution_id,
            "task_id": e.task_id,
            "phase": e.phase,
            "message": e.message,
        }
        for e in reversed(events)
    ]
