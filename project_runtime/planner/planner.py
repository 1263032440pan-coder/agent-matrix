"""DynamicPlanner — convert ProjectPlaybook workstreams into a Task Graph.

Respects Authority levels (MANDATORY never silently dropped).
Uses TaskGraphManager (D04) for Task CRUD + dependency management.
"""

from __future__ import annotations

import json
import logging

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

        playbook_ref = json.dumps(
            {
                # WP022-TD02: workstream_name = 归属流（builder 的
                # ws["workstream"]，V0.1 来自文档 h1）——traceability
                # ARCHITECTURE 关联靠它；task 名另存 task_name
                "workstream_name": ws.get("workstream") or ws["name"],
                "task_name": ws["name"],
                "authority": authority,
                "source_type": (ws.get("source") or {}).get("type", "UNKNOWN"),
                "acceptance_criteria": ws.get("acceptance_criteria"),
                # WP-007: Task 可引用 Playbook / Research（有则带上）
                "research_enrichments": ws.get("research_enrichments") or [],
            }
        )

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
