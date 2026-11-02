"""Project dashboard API (D17 read-only + minimal write actions for demo)."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.project_state.enums import (
    AutomationMode,
    GateStatus,
    ProjectStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    GateResult,
    MethodGap,
    Project,
    ProjectPlaybook,
    ProjectProcedureModel,
    ProjectReadinessAssessment,
    Task,
    TaskRun,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def get_session():
    async with AsyncSession(engine) as session:
        yield session


class CreateProjectRequest(BaseModel):
    name: str
    automation_mode: str = "FULL_AUTO"
    # 用户工作区绝对路径；创建时指定。空则回落 data/tasks/{id}
    workspace_path: str | None = None


class ModeUpdateRequest(BaseModel):
    automation_mode: str


@router.post("", status_code=201)
async def create_project(
    req: CreateProjectRequest, session: AsyncSession = Depends(get_session)
):
    if req.automation_mode not in ("FULL_AUTO", "MANUAL"):
        raise HTTPException(400, "automation_mode must be FULL_AUTO or MANUAL")
    ws_stored: str | None = None
    if req.workspace_path and str(req.workspace_path).strip():
        from project_runtime.workspace import validate_workspace_path

        try:
            resolved = validate_workspace_path(
                str(req.workspace_path), must_exist=False
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / ".agent-matrix" / "tasks").mkdir(
            parents=True, exist_ok=True
        )
        ws_stored = str(resolved)
    project = Project(
        name=req.name,
        automation_mode=AutomationMode(req.automation_mode),
        status=ProjectStatus.CREATED,
        workspace_path=ws_stored,
    )
    session.add(project)
    await session.flush()
    pid, pname = project.id, project.name
    ws_out = project.workspace_path
    await session.commit()
    return {"id": pid, "name": pname, "workspace_path": ws_out}


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
        from project_runtime.execution.service import PLAN_STATUS_READY

        ps = getattr(project, "plan_status", None)
        if ps is not None and ps != PLAN_STATUS_READY:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error_code": "PLAN_NOT_READY",
                    "message": f"计划未就绪（plan_status={ps}），拒绝执行",
                    "plan_status": ps,
                },
            )
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


async def _stage_last_model(session: AsyncSession, project_id: int) -> int:
    """最近一次 procedure model id（阶段间接力）。"""
    stmt = (
        select(ProjectProcedureModel.id)
        .where(ProjectProcedureModel.project_id == project_id)
        .order_by(ProjectProcedureModel.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one()


async def _stage_last_assessment(
    session: AsyncSession, project_id: int, model_id: int
) -> int:
    """最近一次 readiness assessment id。"""
    stmt = (
        select(ProjectReadinessAssessment.id)
        .where(ProjectReadinessAssessment.project_id == project_id)
        .order_by(ProjectReadinessAssessment.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one()


async def _stage_last_playbook(session: AsyncSession, project_id: int):
    """最近一次 playbook（planner 阶段接力）。"""
    stmt = (
        select(ProjectPlaybook)
        .where(ProjectPlaybook.project_id == project_id)
        .order_by(ProjectPlaybook.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one()


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

    # 两段短事务（RH-02 同款范式）: 每阶段独立 session/commit——
    # LLM 流式期间（可达 5-10 分钟）不持有 SQLite 写锁，
    # 否则 LLM_THINKING_CHUNK 事件（独立短事务）全部 database is locked。
    async def _stage(fn):
        async with AsyncSession(engine) as session:
            result = await fn(session)
            await session.commit()
            return result

    async def _clear_old_plan(session: AsyncSession):
        """重新规划清场（用户决策 2026-10-09: 方案 1 全清）。

        同一项目内再次「生成计划」= 换一版方案——本项目全部旧任务
        （含 ACCEPTED）连同依赖边、执行记录、产物引用一起删除，
        不再叠加多轮规划的任务图。项目/方法资产/研究记录保留。
        删除序: 间接表（task_id 引用）先删，task 后删。
        """
        from sqlalchemy import select as _select

        from project_runtime.project_state.models import Task

        task_ids = list(
            (
                await session.execute(
                    _select(Task.id).where(Task.project_id == project_id)
                )
            ).scalars().all()
        )
        if not task_ids:
            project = await session.get(Project, project_id)
            if project is not None:
                project.plan_status = "ASSIGNING"
            return {"cleared_tasks": 0}
        idlist = ",".join(str(t) for t in task_ids)

        # 间接表（task_id / execution 经 task 引用）
        from project_runtime.execution.models import Execution

        exec_ids = list(
            (
                await session.execute(
                    _select(Execution.id).where(Execution.task_id.in_(task_ids))
                )
            ).scalars().all()
        )

        for table, col in [
            ("task_run", "task_id"),
            ("gate_result", "task_id"),
            ("manual_action", "task_id"),
            ("task_dependency", "task_id"),
            ("issue", "task_id"),
        ]:
            await session.execute(
                _text(
                    f'DELETE FROM "{table}" WHERE "{col}" IN ({idlist})'
                )
            )
        if exec_ids:
            exlist = ",".join(str(e) for e in exec_ids)
            # runtime_event 有 execution_id 列; artifact/event 只有 task_id
            # （execution 间接引用）——按各表实有列删（V1.5a-1 集成实测
            # 修复: 曾统一用 execution_id IN (...) 对三表删除, 项目带
            # 历史 execution 时 no such column: execution_id 直接 502）
            await session.execute(
                _text(
                    f'DELETE FROM "runtime_event" '
                    f'WHERE execution_id IN ({exlist}) '
                    f'OR task_id IN ({idlist})'
                )
            )
            for table in ("artifact", "event"):
                await session.execute(
                    _text(
                        f'DELETE FROM "{table}" '
                        f'WHERE task_id IN ({idlist})'
                    )
                )
        else:
            for table in ("artifact", "event", "runtime_event"):
                await session.execute(
                    _text(
                        f'DELETE FROM "{table}" WHERE task_id IN ({idlist})'
                    )
                )
        await session.execute(
            _text(f'DELETE FROM execution WHERE task_id IN ({idlist})')
        )
        await session.execute(
            _text(f'DELETE FROM task WHERE id IN ({idlist})')
        )
        # SYS-106: 清场后进入认领中（执行入口拒绝直至 READY）
        project = await session.get(Project, project_id)
        if project is not None:
            project.plan_status = "ASSIGNING"
        logger.info(
            "plan clear: project %d removed %d old tasks (full reset)",
            project_id, len(task_ids),
        )
        return {"cleared_tasks": len(task_ids)}

    async def _normalize(session: AsyncSession):
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        # V1.6 序1: 交付画像语义判定先行（best-effort——失败不阻断规划,
        # 双失败保守 NOT_SOFTWARE, UI 手动重判兜底）
        profile = None
        try:
            from project_runtime.procedure.delivery_classifier import (
                DeliveryClassifier,
            )

            profile = await DeliveryClassifier(session).classify_and_store(
                project_id
            )
        except Exception:  # noqa: BLE001
            logger.debug("delivery classify failed", exc_info=True)
        # V1.6 序2: profile 透传 extractor（SOFTWARE_DEV → prompt 基线约定）
        normalizer = ProcedureNormalizer.with_llm(
            session, project_id=project_id, delivery_profile=profile,
        )
        model = await normalizer.normalize(project_id)
        return {"model_id": model.id, "delivery_profile": profile}

    async def _assess(session: AsyncSession):
        model = await _stage_last_model(session, project_id)
        assessment = await MethodReadinessAssessor(session).assess(project_id, model)
        return {"assessment_id": assessment.id}

    async def _classify(session: AsyncSession):
        model = await _stage_last_model(session, project_id)
        assessment = await _stage_last_assessment(session, project_id, model)
        gaps = await GapClassifier.with_llm(session).classify(assessment)
        return {"gaps": len(gaps), "assessment_id": assessment}

    async def _resolve(session: AsyncSession):
        """Gap 路由（首次规划即研究，用户需求 2026-10-09）。

        分类出的 gap 逐个路由: PRIVATE 内部消化 / MANDATORY 阻塞 /
        可路由类型（DOMAIN/TECHNICAL/PROCESS）五查 miss 时联网研究。
        有可用 skill（项目绑定或注册表可解析）时不联网——skill 已提供
        方法，研究只是补充信息（research_executor=False 走创建不执行）。
        """
        from sqlalchemy import select as _select

        from project_runtime.procedure.skill_planning import (
            _project_doc_text,
        )
        from project_runtime.research.router import GapResolver
        from project_runtime.skills.enums import SkillType
        from project_runtime.skills.selector import SkillSelector

        # skill 可用性（用户修正 2026-10-09: 必须是符合任务场景的 skill，
        # 不是"注册表非空"）:
        #   1. 项目显式绑定 PROJECT_METHOD skill（用户意志）→ 可用
        #   2. 无绑定 → LLM 按项目文档内容自动选（function calling，
        #      与 read_skill_stages 同一选择语义）选中才算可用
        # 注册表有 skill 但都不匹配场景 → 仍联网研究
        doc = await _project_doc_text(session, project_id)
        selector = SkillSelector(session)
        matched = await selector.select(
            task_content=doc,
            skill_type=SkillType.PROJECT_METHOD,
            extra_context="项目流程规划：判断是否存在匹配本项目的方法类 skill",
        )
        skill_available = matched is not None

        model = await _stage_last_model(session, project_id)
        assessment = await _stage_last_assessment(session, project_id, model)
        gap_rows = (
            await session.execute(
                _select(MethodGap.id).where(
                    MethodGap.assessment_id == assessment
                )
            )
        ).scalars().all()

        resolver = GapResolver(
            session, research_executor=False if skill_available else None
        )
        answered = 0
        for gid in gap_rows:
            try:
                resolution = await resolver.resolve_gap(gid)
                if resolution.action == "RESEARCH_ANSWERED":
                    answered += 1
            except Exception:  # noqa: BLE001 — 单 gap 路由失败不阻断
                logger.debug("gap resolve failed for %s", gid, exc_info=True)
        return {
            "resolved_gaps": len(gap_rows),
            "research_answered": answered,
            "skill_available": skill_available,
        }

    async def _extract_manifest(session: AsyncSession):
        """V1.6c: 材料+skill → 项目 delivery_manifest；非法 JSON → 422 toast。"""
        import json as _json

        from project_runtime.event import EventEngine, RuntimeEventType
        from project_runtime.procedure.delivery_manifest import (
            DeliveryManifestError,
            extract_manifest_from_materials,
            manifest_skill_hint_for_profile,
        )
        from project_runtime.procedure.skill_planning import (
            _project_doc_text,
        )

        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        materials = await _project_doc_text(session, project_id)
        if not (materials or "").strip():
            project.delivery_manifest = None
            return {"delivery_manifest": None, "deliverables": 0}

        skill_hint = manifest_skill_hint_for_profile(
            project.delivery_profile,
        )
        try:
            man = await extract_manifest_from_materials(
                materials_text=materials,
                skill_hint=skill_hint,
            )
        except DeliveryManifestError as e:
            project.plan_status = "INVALID"
            await session.commit()
            try:
                await EventEngine().record(
                    RuntimeEventType.DELIVERY_MANIFEST_INVALID,
                    project_id=project_id,
                    phase="PLAN",
                    message=e.message,
                    payload={"error_code": e.error_code},
                )
            except Exception:  # noqa: BLE001
                logger.debug("emit DELIVERY_MANIFEST_INVALID failed",
                             exc_info=True)
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error_code": e.error_code,
                    "message": e.message,
                },
            ) from e
        project.delivery_manifest = _json.dumps(
            man, ensure_ascii=False,
        )
        return {
            "delivery_manifest": "ok",
            "deliverables": len(man.get("deliverables") or []),
        }

    async def _generate_validator(session: AsyncSession):
        """V1.6d: 冻结校验脚本；失败 → 422 DELIVERY_VALIDATOR_INVALID。"""
        from project_runtime.event import EventEngine, RuntimeEventType
        from project_runtime.procedure.delivery_manifest import (
            validate_manifest,
        )
        from project_runtime.procedure.delivery_validator import (
            DeliveryValidatorError,
            clear_validator_files,
            generate_and_install_validator,
            utcnow,
        )
        from project_runtime.procedure.skill_planning import (
            _project_doc_text,
        )

        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        # 先清旧（脏态防护）
        clear_validator_files(project_id)
        project.delivery_validator_hash = None
        project.delivery_validator_generated_at = None
        await session.flush()

        if not project.delivery_manifest:
            err = DeliveryValidatorError(
                "无交付清单，无法生成校验脚本（请确认已上传材料）"
            )
            try:
                await EventEngine().record(
                    RuntimeEventType.DELIVERY_VALIDATOR_INVALID,
                    project_id=project_id,
                    phase="PLAN",
                    message=err.message,
                    payload={"error_code": err.error_code},
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "emit DELIVERY_VALIDATOR_INVALID failed",
                    exc_info=True,
                )
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error_code": err.error_code,
                    "message": err.message,
                },
            ) from err

        try:
            man = validate_manifest(project.delivery_manifest)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error_code": "DELIVERY_VALIDATOR_INVALID",
                    "message": f"交付清单无法用于生成校验脚本：{e}",
                },
            ) from e

        materials = await _project_doc_text(session, project_id)
        skill_hint = ""
        if project.delivery_profile == "SOFTWARE_DEV":
            skill_hint = (
                "按清单 match 做存在性校验；不得弱于 ext/"
                "name_contains/any_name_prefix/min_count。"
            )
        try:
            digest = await generate_and_install_validator(
                project_id,
                man,
                materials_text=materials or "",
                skill_hint=skill_hint,
            )
        except DeliveryValidatorError as e:
            clear_validator_files(project_id)
            project.delivery_validator_hash = None
            project.delivery_validator_generated_at = None
            project.plan_status = "INVALID"
            # 必须先 commit：否则 _stage 因 HTTPException 回滚，
            # plan_status 会永远卡在清场时的 ASSIGNING，执行入口一直 409。
            await session.commit()
            try:
                await EventEngine().record(
                    RuntimeEventType.DELIVERY_VALIDATOR_INVALID,
                    project_id=project_id,
                    phase="PLAN",
                    message=e.message,
                    payload={"error_code": e.error_code},
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "emit DELIVERY_VALIDATOR_INVALID failed",
                    exc_info=True,
                )
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error_code": e.error_code,
                    "message": e.message,
                },
            ) from e
        project.delivery_validator_hash = digest
        project.delivery_validator_generated_at = utcnow()
        return {
            "delivery_validator": "ok",
            "delivery_validator_hash": digest[:8],
        }

    async def _build(session: AsyncSession):
        playbook = await PlaybookBuilder(session).build(project_id)
        return {"playbook_id": playbook.id}

    async def _plan(session: AsyncSession):
        playbook = await _stage_last_playbook(session, project_id)
        tasks = await DynamicPlanner(session).plan(playbook)
        project = await session.get(Project, project_id)
        if project is not None:
            project.plan_status = "ASSIGNING"
        # 20号规范 §2.2 路径1: 规划后按任务环节自动建 Agent 实例
        # （best-effort——Agent 面故障绝不阻断规划落账）
        from project_runtime.worker.lifecycle import provision_after_plan

        agents = provision_after_plan(
            session, project_id, [t.task_type for t in tasks]
        )
        try:
            spawned = await agents
        except Exception:  # noqa: BLE001
            spawned = 0
        return {"tasks": len(tasks), "agents_provisioned": spawned}

    async def _assign_deliverables_phase() -> dict:
        """SYS-107~112: LLM 不持事务；硬门通过后短事务写入 deliverable_ids。"""
        from project_runtime.event import EventEngine, RuntimeEventType
        from project_runtime.procedure.delivery_assignment import (
            DeliveryAssignmentError,
            complete_delivery_assignment,
            default_llm_fn,
        )
        from project_runtime.procedure.plan_decompose import PlanDecomposeError

        try:
            return await complete_delivery_assignment(
                project_id,
                llm=default_llm_fn,
                freeze_accepted=False,
            )
        except (DeliveryAssignmentError, PlanDecomposeError) as e:
            evt = (
                RuntimeEventType.PLAN_DECOMPOSE_INVALID
                if isinstance(e, PlanDecomposeError)
                else RuntimeEventType.DELIVERY_ASSIGNMENT_INVALID
            )
            try:
                await EventEngine().record(
                    evt,
                    project_id=project_id,
                    phase="PLAN",
                    message=e.message,
                    payload={
                        "error_code": e.error_code,
                        "problems": e.problems[:20],
                    },
                )
            except Exception:  # noqa: BLE001
                logger.debug("emit plan gate invalid failed", exc_info=True)
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error_code": e.error_code,
                    "message": e.message,
                    "problems": e.problems[:20],
                },
            ) from e

    result: dict = {}
    try:
        from project_runtime.event.activity import emit_activity

        async def _phase(label: str, coro):
            await emit_activity(
                project_id,
                f"规划阶段：{label}…",
                phase=f"plan:{label}",
                throttle=False,
            )
            return await coro

        result.update(await _phase("清理旧计划", _stage(_clear_old_plan)))
        result.update(await _phase("材料抽取/归一", _stage(_normalize)))
        result.update(await _phase("抽取交付清单", _stage(_extract_manifest)))
        result.update(await _phase("生成校验脚本", _stage(_generate_validator)))
        result.update(await _phase("评估缺口", _stage(_assess)))
        result.update(await _phase("缺口分类", _stage(_classify)))
        result.update(await _phase("缺口研究/解析", _stage(_resolve)))
        result.update(await _phase("构建 Playbook", _stage(_build)))
        result.update(await _phase("生成任务图", _stage(_plan)))
        result.update(
            await _phase("交付认领", _assign_deliverables_phase())
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
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
            # V1.6 序6: 交付画像（UI 标识 + 重新判定入口数据源）
            "delivery_profile": p.delivery_profile,
            "workspace_path": p.workspace_path,
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


@router.post("/{project_id}/reclassify-delivery", status_code=200)
async def reclassify_delivery(project_id: int):
    """V1.6 序6: 手动重新判定交付画像（P2-3——误判兜底通道）。

    重跑 delivery_classifier 并落库; 判定后由下次 /plan 生效注入。
    """
    from project_runtime.procedure.delivery_classifier import DeliveryClassifier

    async with AsyncSession(engine) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        profile = await DeliveryClassifier(session).classify_and_store(
            project_id
        )
        await session.commit()
    return {"project_id": project_id, "delivery_profile": profile}


class UserReworkBody(BaseModel):
    cascade_hard_downstream: bool = False
    dry_run: bool = False


@router.post("/{project_id}/tasks/{task_id}/user-rework", status_code=200)
async def user_rework_task(
    project_id: int, task_id: int, body: UserReworkBody,
):
    """SYS-143: 用户单节点 Rework（确认框 dry_run + 提交）。"""
    from project_runtime.execution.service import ExecutionService
    from project_runtime.execution.user_rework import (
        UserReworkConflictError,
        UserReworkNotFoundError,
        preview_user_rework,
        run_user_rework,
    )

    async with AsyncSession(engine) as session:
        try:
            if body.dry_run:
                out = await preview_user_rework(
                    session,
                    project_id,
                    task_id,
                    cascade_hard_downstream=body.cascade_hard_downstream,
                )
                return out
            out = await run_user_rework(
                session,
                project_id,
                task_id,
                cascade_hard_downstream=body.cascade_hard_downstream,
                submit=False,
            )
            await session.commit()
        except UserReworkNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except UserReworkConflictError as e:
            raise HTTPException(409, str(e)) from e

    if out.get("should_submit"):
        try:
            await ExecutionService().submit(project_id, task_id)
        except Exception:  # noqa: BLE001
            logger.debug("user-rework submit failed", exc_info=True)
    return out


_THINKING_STREAM_TYPES = (
    "LLM_THINKING_CHUNK",
    "LLM_ANSWER_CHUNK",
    "LLM_TOOL_CALL",
    "LLM_TOOL_RESULT",
    "AGENT_ACTIVITY",
)
_THINKING_KIND = {
    "LLM_THINKING_CHUNK": "thinking",
    "LLM_ANSWER_CHUNK": "answer",
    "LLM_TOOL_CALL": "tool",
    "LLM_TOOL_RESULT": "tool",
    "AGENT_ACTIVITY": "activity",
}


@router.get("/{project_id}/agent-thinking")
async def agent_thinking_stream(
    project_id: int, after_id: int = 0, limit: int = 100,
):
    """项目级运行动态流（ThinkingPanel）：思考 / 正文 / 工具 / 活动。

    payload.text 优先；否则用 message。kind 供前端分样式。
    """
    from project_runtime.event.models import RuntimeEvent

    async with AsyncSession(engine) as session:
        stmt = (
            select(RuntimeEvent)
            .where(
                RuntimeEvent.project_id == project_id,
                RuntimeEvent.event_type.in_(_THINKING_STREAM_TYPES),
                RuntimeEvent.id > after_id,
            )
            .order_by(RuntimeEvent.id.desc())
            .limit(limit)
        )
        events = list(
            (await session.execute(stmt)).scalars().all()
        )

        def _row(e: RuntimeEvent) -> dict:
            payload: dict = {}
            if e.payload:
                try:
                    payload = json.loads(e.payload) or {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
            text = payload.get("text")
            if not text:
                text = e.message
            return {
                "event_id": e.id,
                "task_id": e.task_id,
                "timestamp": (
                    e.timestamp.isoformat() if e.timestamp else None
                ),
                "kind": _THINKING_KIND.get(e.event_type, "activity"),
                "event_type": e.event_type,
                "text": text,
                "agent_instance_id": payload.get("agent_instance_id"),
                "agent_name": payload.get("agent_name"),
            }

        return {
            "events": [_row(e) for e in reversed(events)],
            "last_event_id": events[0].id if events else after_id,
        }


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
    # V1.6c: 解析项目总清单，供节点「要求产物」展示
    manifest = None
    if project.delivery_manifest:
        try:
            from project_runtime.procedure.delivery_manifest import (
                validate_manifest,
            )

            manifest = validate_manifest(project.delivery_manifest)
        except Exception:  # noqa: BLE001
            logger.debug("dashboard manifest parse failed", exc_info=True)
            manifest = None
    # 旧计划未把 LLM outputs 写入 playbook_ref：按任务名回填流程节点 outputs
    node_outputs_by_name: dict[str, str] = {}
    try:
        from project_runtime.project_state.models import ProcedureNode

        model_id = (
            await session.execute(
                select(ProjectProcedureModel.id)
                .where(ProjectProcedureModel.project_id == project_id)
                .order_by(ProjectProcedureModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if model_id is not None:
            for n in (
                await session.execute(
                    select(ProcedureNode).where(
                        ProcedureNode.model_id == model_id
                    )
                )
            ).scalars().all():
                if n.outputs:
                    node_outputs_by_name[n.name] = n.outputs
    except Exception:  # noqa: BLE001
        logger.debug("dashboard node outputs lookup failed", exc_info=True)
    # SYS-140/161: 最新 soft gap + 每节点 attempt/返工次数
    gap_by_task: dict[int, dict] = {}
    attempt_by_task: dict[int, int] = {}
    try:
        from project_runtime.project_state.models import TaskRun as _TaskRunDash

        if tasks:
            run_rows = (
                await session.execute(
                    select(_TaskRunDash)
                    .where(_TaskRunDash.task_id.in_([t.id for t in tasks]))
                    .order_by(_TaskRunDash.id.desc())
                )
            ).scalars().all()
            seen_tid: set[int] = set()
            for run in run_rows:
                att = int(getattr(run, "attempt", 1) or 1)
                prev = attempt_by_task.get(run.task_id, 0)
                if att > prev:
                    attempt_by_task[run.task_id] = att
                if run.task_id in seen_tid:
                    continue
                seen_tid.add(run.task_id)
                if not run.result:
                    continue
                try:
                    payload = json.loads(run.result)
                except (TypeError, ValueError):
                    continue
                gap = (payload or {}).get("delivery_gap")
                if isinstance(gap, dict) and gap.get("soft_accepted"):
                    gap_by_task[run.task_id] = gap
    except Exception:  # noqa: BLE001
        logger.debug("dashboard delivery_gap lookup failed", exc_info=True)

    fail_by_task: dict[int, int] = {}
    try:
        if tasks:
            for tid, n in (
                await session.execute(
                    select(GateResult.task_id, func.count(GateResult.id))
                    .where(
                        GateResult.task_id.in_([t.id for t in tasks]),
                        GateResult.status == GateStatus.FAIL,
                    )
                    .group_by(GateResult.task_id)
                )
            ).all():
                fail_by_task[int(tid)] = int(n)
    except Exception:  # noqa: BLE001
        logger.debug("dashboard gate fail count failed", exc_info=True)

    task_list = [
        {
            "id": t.id,
            "name": t.name,
            "status": t.status.value,
            "depends_on": deps_by_task.get(t.id, []),
            "required_deliverables": _required_deliverables_of(
                t.playbook_ref,
                manifest,
                node_outputs_by_name.get(t.name),
            ),
            "stage_outputs": _stage_outputs_of(
                t.playbook_ref,
                node_outputs_by_name.get(t.name),
            ),
            "delivery_gap": gap_by_task.get(t.id),
            "verification_level": _verification_level(
                project_id, t.id,
                workspace_path=project.workspace_path,
            ),
            **_task_delivery_stats(
                t, attempt_by_task, fail_by_task, gap_by_task,
            ),
            **_task_load_fields(t.playbook_ref),
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
        # V1.6 序6: 交付画像 + 三件状态（事实汇总——UI 状态行）
        "delivery_profile": project.delivery_profile,
        # V1.6d: 冻结校验脚本元数据
        "delivery_validator_hash": project.delivery_validator_hash,
        "delivery_validator_hash_prefix": (
            (project.delivery_validator_hash or "")[:8] or None
        ),
        "delivery_validator_generated_at": (
            project.delivery_validator_generated_at.isoformat()
            if project.delivery_validator_generated_at
            else None
        ),
        "plan_status": getattr(project, "plan_status", None) or "READY",
        "delivery_assignment": _delivery_assignment_view(
            manifest, tasks,
        ),
        "delivery_checklist": _delivery_checklist_view(
            project_id, manifest, tasks, artifacts,
            workspace_path=project.workspace_path,
        ),
        "workspace_path": project.workspace_path,
        "delivery_quality": await _delivery_quality(
            session, tasks, gap_by_task,
        ),
        "delivery_status": {
            "ppt": any(
                (a.name or "").lower().endswith(".pptx") for a in artifacts
            ),
            "code": any(
                (a.name or "").lower().endswith((".py", ".js", ".ts"))
                for a in artifacts
            ) and any(
                (a.name or "").lower().startswith("readme")
                for a in artifacts
            ),
            "test_report": any(
                "测试" in (a.name or "") and (a.name or "").lower().endswith(".md")
                for a in artifacts
            ),
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


_TAG_LABELS = {
    "ppt": "方案PPT",
    "code": "可运行代码",
    "test_report": "测试报告",
}
_RE_PPT = re.compile(r"ppt|pptx|演示|汇报|幻灯|slides?", re.I)
_RE_CODE = re.compile(r"源码|代码|实现|入口文件|功能模块|README|可运行", re.I)
_RE_TEST = re.compile(r"测试报告|测试文件|测试", re.I)


def _parse_playbook_ref(playbook_ref: str | None) -> dict:
    if not playbook_ref:
        return {}
    try:
        data = json.loads(playbook_ref)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _split_output_blob(raw: object) -> list[str]:
    """与 planner 同构：把 outputs 长串拆成展示用列表。"""
    import re

    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text or text.upper() in ("UNKNOWN", "AMBIGUOUS", "无", "N/A"):
        return []
    return [
        p.strip()
        for p in re.split(r"[；;、\|/\n]+", text)
        if p.strip()
    ]


def _stage_outputs_of(
    playbook_ref: str | None, fallback_outputs: str | None = None,
) -> list[str]:
    ref = _parse_playbook_ref(playbook_ref)
    outs = [str(x) for x in (ref.get("stage_outputs") or []) if x]
    if outs:
        return outs
    # 兼容旧任务：playbook_ref 未透传时，用流程节点 outputs
    return _split_output_blob(ref.get("outputs") or fallback_outputs)


def _aggregate_tool_quality(
    rows: list[tuple[str, str | None]],
) -> dict:
    """SYS-161: 从 LLM_TOOL_* 事件聚合 error_code 频次与截断修复次数。"""
    error_codes: dict[str, int] = {}
    repaired = 0
    for etype, payload in rows:
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if etype == "LLM_TOOL_RESULT":
            code = data.get("error_code")
            if code:
                key = str(code)
                error_codes[key] = error_codes.get(key, 0) + 1
        elif etype == "LLM_TOOL_CALL" and data.get("repaired"):
            repaired += 1
    return {"error_codes": error_codes, "truncated": repaired, "repaired": repaired}


def _task_delivery_stats(
    task,
    attempt_by_task: dict[int, int],
    fail_by_task: dict[int, int],
    gap_by_task: dict[int, dict],
) -> dict:
    """SYS-161: 节点级首轮交付 / 返工次数 / 是否软放行。"""
    attempt = int(attempt_by_task.get(task.id, 1) or 1)
    fails = int(fail_by_task.get(task.id, 0) or 0)
    rework_count = max(0, attempt - 1, fails)
    soft = bool(gap_by_task.get(task.id))
    first_pass = (
        task.status.value == "ACCEPTED" and rework_count == 0 and not soft
    )
    return {
        "run_attempt": attempt,
        "rework_count": rework_count,
        "first_pass": first_pass,
        "soft_accepted": soft,
    }


async def _delivery_quality(session, tasks, gap_by_task: dict) -> dict:
    """SYS-161: 项目级交付质量只读度量。"""
    from project_runtime.event.models import RuntimeEvent

    accepted = sum(1 for t in tasks if t.status.value == "ACCEPTED")
    soft = len(gap_by_task)
    out = {
        "accepted": accepted,
        "soft_accepted": soft,
        "soft_rate": (
            round(soft / max(1, accepted), 3) if accepted else 0.0
        ),
        "first_pass": 0,
        "first_pass_rate": 0.0,
        "error_codes": {},
        "truncated": 0,
        "repaired": 0,
    }
    task_ids = [t.id for t in tasks]
    if not task_ids:
        return out
    # first_pass：ACCEPTED、无 soft、attempt<=1 且无 Gate FAIL
    attempt_by: dict[int, int] = {}
    for tid, att in (
        await session.execute(
            select(TaskRun.task_id, func.max(TaskRun.attempt))
            .where(TaskRun.task_id.in_(task_ids))
            .group_by(TaskRun.task_id)
        )
    ).all():
        attempt_by[int(tid)] = int(att or 1)
    fail_by: dict[int, int] = {}
    for tid, n in (
        await session.execute(
            select(GateResult.task_id, func.count(GateResult.id))
            .where(
                GateResult.task_id.in_(task_ids),
                GateResult.status == GateStatus.FAIL,
            )
            .group_by(GateResult.task_id)
        )
    ).all():
        fail_by[int(tid)] = int(n)
    first_pass = 0
    for t in tasks:
        if t.status.value != "ACCEPTED" or t.id in gap_by_task:
            continue
        att = attempt_by.get(t.id, 1)
        fails = fail_by.get(t.id, 0)
        if max(0, att - 1, fails) == 0:
            first_pass += 1
    out["first_pass"] = first_pass
    out["first_pass_rate"] = (
        round(first_pass / max(1, accepted), 3) if accepted else 0.0
    )
    rows = (
        await session.execute(
            select(RuntimeEvent.event_type, RuntimeEvent.payload)
            .where(
                RuntimeEvent.task_id.in_(task_ids),
                RuntimeEvent.event_type.in_(
                    ("LLM_TOOL_RESULT", "LLM_TOOL_CALL"),
                ),
            )
            .order_by(RuntimeEvent.id.desc())
            .limit(2000)
        )
    ).all()
    out.update(_aggregate_tool_quality([(r[0], r[1]) for r in rows]))
    return out


def _verification_level(
    project_id: int,
    task_id: int,
    *,
    workspace_path: str | None = None,
) -> str:
    """SYS-159: 只读验证等级（compile / syntax / none），不进 Gate。"""
    from project_runtime.workspace import resolve_task_workspace

    root = resolve_task_workspace(project_id, task_id, workspace_path)
    if not root.exists():
        return "none"
    names = [p.name.lower() for p in root.rglob("*") if p.is_file()]
    if any(n in ("pom.xml", "build.gradle") for n in names):
        return "compile"
    if any(n.endswith((".py", ".ts", ".js")) for n in names) or "package.json" in names:
        return "syntax"
    return "none"


def _task_load_fields(playbook_ref: str | None) -> dict:
    """SYS-125: Dashboard 负荷 / 令牌 / 拆分标记。"""
    ref = _parse_playbook_ref(playbook_ref)
    tokens = ref.get("obligation_tokens") or []
    token_ids = []
    for t in tokens:
        if isinstance(t, dict) and t.get("id"):
            token_ids.append(str(t["id"]))
        elif isinstance(t, str):
            token_ids.append(t)
    load = ref.get("task_load")
    if load is None and tokens:
        try:
            from project_runtime.procedure.task_load import compute_task_load

            load = compute_task_load(
                [t if isinstance(t, dict) else {"id": t} for t in tokens]
            )
        except Exception:  # noqa: BLE001
            load = None
    return {
        "task_load": load,
        "load_max": ref.get("load_max"),
        "obligation_token_ids": token_ids,
        "bin_packed": bool(ref.get("bin_packed")),
        "decomposed_from": ref.get("decomposed_from"),
    }


def _required_deliverables_of(
    playbook_ref: str | None,
    manifest: dict | None,
    fallback_outputs: str | None = None,
) -> list[dict]:
    """任务被要求的产物（优先 deliverable_ids → tags → stage_outputs/outputs）。"""
    ref = _parse_playbook_ref(playbook_ref)
    by_id: dict = {}
    if manifest:
        by_id = {d["id"]: d for d in (manifest.get("deliverables") or [])}

    items: list[dict] = []
    ids = [str(x) for x in (ref.get("deliverable_ids") or []) if x]
    if not ids and manifest:
        tags = list(ref.get("delivery_tags") or [])
        # tags 空时，用产物文案再扫一遍（旧计划未写 tags）
        if not tags:
            blob = " ".join(
                str(x)
                for x in [
                    ref.get("task_name"),
                    fallback_outputs,
                    *(ref.get("stage_outputs") or []),
                    ref.get("outputs"),
                ]
                if x
            )
            if _RE_PPT.search(blob):
                tags.append("ppt")
            if _RE_CODE.search(blob):
                tags.append("code")
            if _RE_TEST.search(blob):
                tags.append("test_report")
        if tags:
            from project_runtime.procedure.delivery_manifest import (
                map_legacy_tags_to_ids,
            )

            ids = map_legacy_tags_to_ids(manifest, tags)

    for did in ids:
        d = by_id.get(did)
        if d:
            items.append({
                "id": did,
                "name": d.get("name") or did,
                "match": d.get("match"),
            })
        else:
            items.append({"id": did, "name": did, "match": None})

    if not items:
        for tag in ref.get("delivery_tags") or []:
            items.append({
                "id": str(tag),
                "name": _TAG_LABELS.get(str(tag), str(tag)),
                "match": None,
            })

    if not items:
        for so in _stage_outputs_of(playbook_ref, fallback_outputs):
            items.append({"id": None, "name": str(so), "match": None})

    # 去重（按 name）
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        key = it.get("name") or it.get("id") or ""
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def _delivery_checklist_view(
    project_id: int,
    manifest: dict | None,
    tasks: list,
    artifacts: list,
    *,
    workspace_path: str | None = None,
) -> dict:
    """概览「产物清单实时对比」：清单项 vs 工作区/产物命中。"""
    from datetime import datetime, timezone

    from project_runtime.procedure.delivery_manifest import (
        check_match,
        list_match_hits,
        project_workspace_rel_paths,
    )
    from project_runtime.workspace import resolve_project_workspace

    if not manifest:
        return {
            "updated_at": None,
            "total": 0,
            "matched": 0,
            "items": [],
        }

    assign = _delivery_assignment_view(manifest, tasks)
    owner_by_id = {str(i["id"]): i for i in assign["items"]}
    task_by_id = {t.id: t for t in tasks}

    files = project_workspace_rel_paths(
        project_id,
        root=resolve_project_workspace(project_id, workspace_path),
    )
    seen = set(files)
    for a in artifacts:
        name = getattr(a, "name", None) or ""
        if name and name not in seen:
            files.append(name)
            seen.add(name)

    items: list[dict] = []
    matched_n = 0
    for d in manifest.get("deliverables") or []:
        did = str(d.get("id"))
        m = d.get("match") if isinstance(d.get("match"), dict) else {}
        problems = check_match(files, m) if m else ["无 match 规则"]
        hits = list_match_hits(files, m)[:24] if m else []
        o = owner_by_id.get(did) or {}
        oid = o.get("owner_task_id")
        ost = None
        if oid is not None and oid in task_by_id:
            ost = task_by_id[oid].status.value
        if not problems:
            status = "matched"
            matched_n += 1
            hint = None
        elif ost in ("RUNNING", "READY", "REWORK", "EXECUTION_DONE"):
            status = "in_progress"
            hint = problems[0]
        elif oid is None or ost in (
            "ACCEPTED", "FAILED", "BLOCKED", "CANCELLED",
        ):
            status = "gap"
            hint = problems[0]
        else:
            status = "pending"
            hint = problems[0]
        items.append({
            "id": did,
            "name": d.get("name"),
            "match": m or None,
            "owner_task_id": oid,
            "owner_task_name": o.get("owner_task_name"),
            "owner_task_status": ost,
            "status": status,
            "matched_paths": hits,
            "hint": hint,
        })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "matched": matched_n,
        "items": items,
    }


def _delivery_assignment_view(
    manifest: dict | None, tasks: list,
) -> dict:
    """SYS-117: 清单项数 / 已分配 / 每项主责。"""
    deliverables = list((manifest or {}).get("deliverables") or [])
    owner: dict[str, dict] = {}
    for t in tasks:
        try:
            pref = json.loads(t.playbook_ref or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for did in pref.get("deliverable_ids") or []:
            owner[str(did)] = {"task_id": t.id, "task_name": t.name}
    items = []
    for d in deliverables:
        did = str(d.get("id"))
        o = owner.get(did)
        items.append({
            "id": did,
            "name": d.get("name"),
            "owner_task_id": o["task_id"] if o else None,
            "owner_task_name": o["task_name"] if o else None,
        })
    assigned = sum(1 for i in items if i["owner_task_id"] is not None)
    uncovered = [i["id"] for i in items if i["owner_task_id"] is None]
    assignment_mode = None
    for t in tasks:
        try:
            pref = json.loads(t.playbook_ref or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        mode = pref.get("assignment_mode")
        if mode:
            assignment_mode = str(mode)
            break
    return {
        "manifest_count": len(items),
        "assigned_count": assigned,
        "uncovered_ids": uncovered,
        "assignment_mode": assignment_mode,
        "items": items,
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
            # CoT UI: 思维链分段全文在 payload（message 只有尾 128 字）
            "payload": json.loads(e.payload) if e.payload else None,
        }
        for e in reversed(events)
    ]
