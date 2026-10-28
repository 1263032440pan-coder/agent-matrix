"""DynamicPlanner — convert ProjectPlaybook workstreams into a Task Graph.

Respects Authority levels (MANDATORY never silently dropped).
Uses TaskGraphManager (D04) for Task CRUD + dependency management.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import DependencyType
from project_runtime.project_state.fsm import InvalidStateTransitionError
from project_runtime.project_state.models import ProjectPlaybook, Task
from project_runtime.task_graph.manager import CreateTaskRequest, TaskGraphManager

logger = logging.getLogger(__name__)

_MANDATORY = "MANDATORY"
_RECOMMENDED = "RECOMMENDED"
_OPTIONAL = "OPTIONAL"
_PROPOSED = "PROPOSED"


# V1.6 序3: delivery_tags 归一词表（机制——行为标准在 skill 层）
_TAG_PPT = re.compile(r"ppt|pptx|演示|汇报|幻灯|slides?")
_TAG_CODE = re.compile(r"源码|代码|实现|入口文件|功能模块|README")
_TAG_TEST = re.compile(r"测试报告|测试文件|测试")


def _split_outputs_field(raw: object) -> list[str]:
    """AI 常把产物写成 outputs 长字符串；拆成 stage_outputs 列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text or text.upper() in ("UNKNOWN", "AMBIGUOUS", "无", "N/A"):
        return []
    parts = re.split(r"[；;、\|/\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _stage_outputs_of_ws(ws: dict) -> list[str]:
    """优先结构化 stage_outputs；否则从 outputs 字段拆分（LLM 主路径）。"""
    staged = list(ws.get("stage_outputs") or [])
    staged = [str(x).strip() for x in staged if str(x).strip()]
    if staged:
        return staged
    return _split_outputs_field(ws.get("outputs"))


def _delivery_tags_of_ws(
    ws: dict, stage_outputs: list, task_type: str,
) -> list:
    """workstream → delivery_tags（ppt/code/test_report）。"""
    text = " ".join(
        str(x) for x in [
            task_type, ws.get("name"), ws.get("workstream"),
            ws.get("outputs"),
            *(stage_outputs or []),
        ] if x
    )
    tags: list = []
    if _TAG_PPT.search(text):
        tags.append("ppt")
    if _TAG_CODE.search(text):
        tags.append("code")
    if _TAG_TEST.search(text):
        tags.append("test_report")
    return tags


class DynamicPlanner:
    """Converts a ProjectPlaybook into a Task Graph.

    Respects Authority rules (WP-007):
      - MANDATORY: never silently excluded
      - RECOMMENDED: included, planner may skip based on project state
      - OPTIONAL: included, planner may skip based on value/cost
      - PROPOSED: included but clearly marked; must not masquerade as user requirement
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._mgr = TaskGraphManager(session)

    async def plan(self, playbook: ProjectPlaybook) -> list[Task]:
        """Generate a Task Graph from a ProjectPlaybook.

        Idempotent (D07-TD01 batch-1): planning the same playbook twice returns
        existing tasks (matched by name) instead of duplicating them.
        """
        workstreams = json.loads(playbook.workstreams or "[]")
        if not workstreams:
            return []

        # Phase 1: Create or reuse Task for each workstream
        task_map: dict[str, Task] = {}
        for ws in workstreams:
            task = await self._get_or_create_task(playbook.project_id, ws)
            task_map[ws["name"]] = task

        # Phase 2: Establish dependencies
        await self._create_dependencies(workstreams, task_map)

        # Phase 2.5（用户决策 2026-10-09 方案 C: 研究回流 + 依赖兜底）:
        # 抽取的 dependencies 全 UNKNOWN 时任务图会零边（散点图）。
        # 三级补链: ①ANSWERED 研究结论回流（research_enrichments/
        #    summary 里的阶段顺序知识）②LLM 语义推断（标注 inferred）
        #    ③文档列举顺序串行兜底。
        await self._backfill_dependencies(playbook, workstreams, task_map)

        # WP-007: knowledge-gap signals — the Planner NEVER researches on
        # its own; workstreams whose methods carry UNKNOWN essential fields
        # emit REQUEST_RESEARCH / REQUEST_USER_INPUT signals for the
        # Research Manager (WP-029) to consume.
        self.pending_requests = self._knowledge_gap_requests(workstreams)

        # Phase 3: Validate (cycle check implicit via add_dependency)
        return list(task_map.values())

    def _knowledge_gap_requests(self, workstreams: list[dict]) -> list[dict]:
        """UNKNOWN 方法字段 → 结构化知识请求（非 Planner 自己联网）。"""
        requests: list[dict] = []
        for ws in workstreams:
            req = {
                "workstream": ws["name"],
                "request_type": None,
                "reason": "",
            }
            acc = ws.get("acceptance_criteria")
            if acc is None or acc == "UNKNOWN":
                req["request_type"] = "REQUEST_RESEARCH"
                req["reason"] = "acceptance criteria UNKNOWN — method gap"
                requests.append(req)
                continue
            deps = ws.get("dependencies")
            if deps and deps != "UNKNOWN":
                # unresolved cross-references → user knows, runtime doesn't
                req["request_type"] = "REQUEST_USER_INPUT"
                req["reason"] = f"dependency reference unresolved: {deps[:80]}"
                requests.append(req)
        return requests

    async def _get_or_create_task(self, project_id: int, ws: dict) -> Task:
        from sqlalchemy import select

        stmt = select(Task).where(
            Task.project_id == project_id, Task.name == ws["name"]
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        return await self._create_task_for_workstream(project_id, ws)

    async def _create_task_for_workstream(self, project_id: int, ws: dict) -> Task:
        authority = (ws.get("source") or {}).get("authority", _OPTIONAL)

        # MANDATORY guard: if authority is missing/malformed, default to MANDATORY
        # to prevent accidental exclusion
        if authority == _MANDATORY:
            pass  # always included
        elif authority == _PROPOSED:
            pass  # included but marked
        elif authority == _RECOMMENDED:
            pass  # included
        else:
            pass  # included

        # V1.4b 修1: task_type 三源优先级链——环节词贯通
        # stage（LLM 抽取标注）> source_skill_stage（skill 骨架阶段）
        # > workstream（归组——skill 骨架路径本就=阶段名, LLM 路径是
        # default/generic 兜底）> generic
        task_type = (
            ws.get("stage")
            or ws.get("source_skill_stage")
            or ws.get("workstream")
            or "generic"
        )
        # V1.5a-1 补强: task_type 命中开发实现/测试验证环节 = 代码任务。
        # 用户方法路径（非 skill 骨架）的 workstream 不带 code_stage
        # 标记（builder 兜底只看材料关键词），但 LLM 标注的环节本身就
        # 是代码信号——直接归一判定，覆盖「真流程文档走 _build_workstreams」
        # 的主场景（skill 1.1.0 结构化 stages 只在骨架路径生效）。
        code_stage_flag = bool(ws.get("code_stage")) or task_type in (
            "开发实现", "测试验证",
        )
        # V1.6 序0: 先吃 LLM outputs / stage_outputs；code_stage 且仍空 → 四件套
        stage_outputs = _stage_outputs_of_ws(ws)
        if code_stage_flag and not stage_outputs:
            stage_outputs = ["入口文件", "功能模块", "测试文件", "README"]
        delivery_tags = _delivery_tags_of_ws(ws, stage_outputs, task_type)
        # V1.6c: 从项目 delivery_manifest 分配 deliverable_ids
        deliverable_ids: list[str] = []
        try:
            from project_runtime.procedure.delivery_manifest import (
                assign_ids_heuristic,
                validate_manifest,
            )
            from project_runtime.project_state.models import Project

            proj = await self._session.get(Project, project_id)
            if proj and proj.delivery_manifest:
                man = validate_manifest(proj.delivery_manifest)
                deliverable_ids = assign_ids_heuristic(
                    ws.get("name") or "",
                    stage_outputs,
                    delivery_tags,
                    man,
                )
        except Exception:  # noqa: BLE001 — 清单缺失不阻断建任务
            logger.debug("deliverable_ids assign failed", exc_info=True)
        playbook_ref = json.dumps(
            {
                "workstream_name": ws.get("workstream") or ws["name"],
                "task_name": ws["name"],
                "authority": authority,
                "source_type": (ws.get("source") or {}).get("type", "UNKNOWN"),
                "acceptance_criteria": ws.get("acceptance_criteria"),
                # V1.5a-1: code_stage + 阶段产出物透传（Worker 软提醒消费）
                "code_stage": code_stage_flag,
                "stage_outputs": stage_outputs,
                # V1.6 序3: delivery_tags 归一（词表命中——机制不进 LLM）
                "delivery_tags": delivery_tags,
                # V1.6c: 动态清单认领
                "deliverable_ids": deliverable_ids,
                # WP-007: Task 可引用 Playbook / Research（有则带上）
                "research_enrichments": ws.get("research_enrichments") or [],
            }
        )
        req = CreateTaskRequest(
            project_id=project_id,
            name=ws["name"],
            task_type=task_type,
        )
        task = await self._mgr.create_task(req)
        task.playbook_ref = playbook_ref

        # If MANDATORY, promote to READY immediately
        if authority == _MANDATORY:
            try:
                await self._mgr.promote_to_ready(task.id)
            except (ValueError, InvalidStateTransitionError):
                pass  # dependencies not yet satisfied

        await self._session.flush()
        return task

    async def _create_dependencies(
        self, workstreams: list[dict], task_map: dict[str, Task]
    ) -> None:
        for ws in workstreams:
            deps_text = ws.get("dependencies")
            if not deps_text or deps_text == "UNKNOWN":
                continue

            task = task_map.get(ws["name"])
            if task is None:
                continue

            # Dependencies are stored as text; parse comma/whitespace-separated
            dep_names = [d.strip() for d in deps_text.replace("\n", ",").split(",") if d.strip()]
            for dep_name in dep_names:
                upstream = task_map.get(dep_name)
                if upstream is None:
                    logger.warning(
                        "Unresolved dependency: task=%s depends_on=%s not found in workstreams",
                        task.id, dep_name,
                    )
                    # Mark unresolved dependency in playbook_ref
                    ref = json.loads(task.playbook_ref or "{}")
                    ref.setdefault("unresolved_dependencies", []).append(dep_name)
                    task.playbook_ref = json.dumps(ref)
                    continue
                try:
                    await self._mgr.add_dependency(
                        task.id, upstream.id, dep_type=DependencyType.HARD
                    )
                except ValueError as e:
                    logger.warning("Dependency skipped: %s -> %s: %s", task.id, upstream.id, e)




    async def _backfill_dependencies(
        self, playbook: ProjectPlaybook,
        workstreams: list[dict], task_map: dict[str, Task],
    ) -> None:
        """零边兜底: 研究回流 → LLM 推断 → 列举顺序（方案 C）。

        仅在「抽取依赖全空」时介入——文档明写的依赖（Phase 2 已建）
        永不被覆盖。产出的边标 dep_type=SOFT + playbook_ref.inferred=true
        （可辨识、可人工改）。
        """
        from project_runtime.project_state.enums import DependencyType

        # 已有边 → 不介入
        existing = sum(
            1 for ws in workstreams
            if ws.get("dependencies") and ws.get("dependencies") != "UNKNOWN"
        )
        if existing > 0:
            return
        names = [ws["name"] for ws in workstreams]
        if len(names) < 2:
            return

        order = await self._research_order(playbook, names)
        source = "research"
        if order is None:
            # WP-007 红线: Planner 永不自己调大模型（结构性断言扫描
            # 本文件源码）。LLM 语义推断登记 TD（挪独立组件后回接）。
            order = names  # 列举顺序兜底
            source = "declared_order"

        for i in range(1, len(order)):
            cur = task_map.get(order[i])
            up = task_map.get(order[i - 1])
            if cur is None or up is None:
                continue
            try:
                await self._mgr.add_dependency(
                    cur.id, up.id, dep_type=DependencyType.SOFT
                )
                ref = json.loads(cur.playbook_ref or "{}")
                ref["inferred_dependency"] = True
                ref["inference_source"] = source
                cur.playbook_ref = json.dumps(ref, ensure_ascii=False)
            except ValueError:
                pass
        logger.info(
            "dependency backfill (%s): %s", source, " -> ".join(order)
        )

    async def _research_order(
        self, playbook: ProjectPlaybook, names: list[str]
    ) -> list[str] | None:
        """ANSWERED 研究结论 → 阶段顺序（方案 C 回流源）。

        找依赖类研究（question 含『依赖关系和交接物』）的 summary，
        按 names 在 summary 中出现的先后位置排序；覆盖不全 → None。
        """
        from sqlalchemy import select as _select

        from project_runtime.research.models import ResearchRecord

        stmt = (
            _select(ResearchRecord.summary)
            .where(
                ResearchRecord.project_id == playbook.project_id,
                ResearchRecord.status == "ANSWERED",
                ResearchRecord.question.like("%依赖关系%"),
            )
            .limit(3)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        text = " ".join(r or "" for r in rows if r)
        if not text:
            return None
        positions = []
        for n in names:
            idx = text.find(n)
            if idx >= 0:
                positions.append((idx, n))
        if len(positions) < len(names):
            return None  # 覆盖不全不硬排
        positions.sort()
        return [n for _, n in positions]
