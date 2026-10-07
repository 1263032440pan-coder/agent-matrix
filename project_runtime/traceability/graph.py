"""TraceabilityGraph (WP-022) — 需求到交付的全链追溯.

七接口: add_node / add_edge / get_upstream / get_downstream /
        get_coverage / find_gaps / rebuild_scope

rebuild_scope 从活数据自动重建全图（投影不是事实源）:
  understanding.known_requirements → REQUIREMENT
  ProcedureNode（workstream）        → ARCHITECTURE
  Task                              → TASK
  Artifact                          → ARTIFACT（TEST 类型并建 TEST 节点）
  GateResult                        → GATE
  DeliveryItem                      → DELIVERY
  边: requirement→task（名称关联 V0.1）/ task→artifact（PRODUCES）/
      task→gate（GATED_BY）/ artifact→delivery（DELIVERS，名称匹配）

Gap 三查: 未实现 Requirement（无下游）/ 无测试 Artifact 的 Task /
无 Gate 的 Task。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import (
    Artifact,
    DeliveryItem,
    GateResult,
    Project,
    Task,
)
from project_runtime.traceability.models import (
    TraceEdge,
    TraceEdgeRel,
    TraceKind,
    TraceNode,
)

logger = logging.getLogger(__name__)


@dataclass
class TraceGap:
    gap_type: str          # UNIMPLEMENTED_REQUIREMENT / NO_TEST_ARTIFACT / NO_GATE
    ref: str
    label: str
    detail: str


@dataclass
class CoverageReport:
    requirements: int = 0
    requirements_implemented: int = 0
    tasks: int = 0
    tasks_with_tests: int = 0
    tasks_with_gate: int = 0
    gaps: list[TraceGap] = field(default_factory=list)

    @property
    def requirement_coverage(self) -> float:
        if not self.requirements:
            return 1.0
        return self.requirements_implemented / self.requirements

    def to_dict(self) -> dict:
        return {
            "requirements": self.requirements,
            "requirements_implemented": self.requirements_implemented,
            "tasks": self.tasks,
            "tasks_with_tests": self.tasks_with_tests,
            "tasks_with_gate": self.tasks_with_gate,
            "requirement_coverage": round(self.requirement_coverage, 3),
            "gaps": [
                {"gap_type": g.gap_type, "ref": g.ref,
                 "label": g.label, "detail": g.detail}
                for g in self.gaps
            ],
        }


class TraceabilityGraph:
    def __init__(self, session: AsyncSession):
        self._session = session

    # ── 核心接口: add / query ────────────────────────────────────────

    async def add_node(
        self, project_id: int, kind: TraceKind, ref_id: str, label: str
    ) -> TraceNode:
        existing = await self._find_node(project_id, kind, ref_id)
        if existing is not None:
            return existing
        node = TraceNode(
            project_id=project_id, kind=kind, ref_id=ref_id, label=label
        )
        self._session.add(node)
        await self._session.flush()
        return node

    async def add_edge(
        self, project_id: int, from_node: TraceNode, to_node: TraceNode,
        rel: TraceEdgeRel, note: str | None = None,
    ) -> TraceEdge:
        stmt = select(TraceEdge).where(
            TraceEdge.from_node_id == from_node.id,
            TraceEdge.to_node_id == to_node.id,
            TraceEdge.rel == rel,
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        edge = TraceEdge(
            project_id=project_id,
            from_node_id=from_node.id, to_node_id=to_node.id,
            rel=rel, note=note,
        )
        self._session.add(edge)
        await self._session.flush()
        return edge

    async def get_upstream(self, node_id: int) -> list[TraceNode]:
        """指向该节点的全部上游（含间接——BFS）。"""
        return await self._walk(node_id, upstream=True)

    async def get_downstream(self, node_id: int) -> list[TraceNode]:
        return await self._walk(node_id, upstream=False)

    async def _walk(self, start: int, *, upstream: bool) -> list[TraceNode]:
        seen: set[int] = set()
        frontier = [start]
        result: list[TraceNode] = []
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur != start:
                node = await self._session.get(TraceNode, cur)
                if node is not None:
                    result.append(node)
            if upstream:
                # 谁指向我: 取 from_node_id where to_node_id == cur
                stmt = select(TraceEdge.from_node_id).where(
                    TraceEdge.to_node_id == cur
                )
            else:
                # 我指向谁: 取 to_node_id where from_node_id == cur
                stmt = select(TraceEdge.to_node_id).where(
                    TraceEdge.from_node_id == cur
                )
            frontier.extend((await self._session.execute(stmt)).scalars().all())
        return result

    # ── 覆盖与缺口 ───────────────────────────────────────────────────

    async def get_coverage(self, project_id: int) -> CoverageReport:
        report = CoverageReport()
        req_nodes = await self._nodes_of(project_id, TraceKind.REQUIREMENT)
        task_nodes = await self._nodes_of(project_id, TraceKind.TASK)
        report.requirements = len(req_nodes)
        report.tasks = len(task_nodes)

        for req in req_nodes:
            downstream = await self.get_downstream(req.id)
            if any(
                n.kind in (TraceKind.TASK, TraceKind.ARCHITECTURE)
                for n in downstream
            ):
                report.requirements_implemented += 1

        for t in task_nodes:
            downstream = await self.get_downstream(t.id)
            has_test = any(n.kind == TraceKind.TEST for n in downstream)
            has_gate = any(n.kind == TraceKind.GATE for n in downstream)
            if has_test:
                report.tasks_with_tests += 1
            if has_gate:
                report.tasks_with_gate += 1

        report.gaps = await self.find_gaps(project_id)
        return report

    async def find_gaps(self, project_id: int) -> list[TraceGap]:
        gaps: list[TraceGap] = []
        req_nodes = await self._nodes_of(project_id, TraceKind.REQUIREMENT)
        task_nodes = await self._nodes_of(project_id, TraceKind.TASK)

        # ① 未实现 Requirement（无 TASK/ARCHITECTURE 下游）
        for req in req_nodes:
            downstream = await self.get_downstream(req.id)
            if not any(
                n.kind in (TraceKind.TASK, TraceKind.ARCHITECTURE)
                for n in downstream
            ):
                gaps.append(TraceGap(
                    gap_type="UNIMPLEMENTED_REQUIREMENT",
                    ref=req.ref_id, label=req.label,
                    detail="需求无任何 Task/Architecture 下游——未实现",
                ))

        # ② 无测试 Artifact 的 Task / ③ 无 Gate 的 Task
        for t in task_nodes:
            downstream = await self.get_downstream(t.id)
            if not any(n.kind == TraceKind.TEST for n in downstream):
                gaps.append(TraceGap(
                    gap_type="NO_TEST_ARTIFACT",
                    ref=t.ref_id, label=t.label,
                    detail="Task 无测试类产物下游",
                ))
            if not any(n.kind == TraceKind.GATE for n in downstream):
                gaps.append(TraceGap(
                    gap_type="NO_GATE",
                    ref=t.ref_id, label=t.label,
                    detail="Task 无 Gate 结果下游",
                ))
        return gaps

    # ── 从活数据重建（投影不是事实源）──────────────────────────────

    async def rebuild_scope(self, project_id: int) -> dict:
        """清空该 project 的图 → 从活数据全量重建。返回统计。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        # 清旧图（边先删——FK）
        node_ids = (
            await self._session.execute(
                select(TraceNode.id).where(TraceNode.project_id == project_id)
            )
        ).scalars().all()
        if node_ids:
            await self._session.execute(
                delete(TraceEdge).where(TraceEdge.from_node_id.in_(node_ids))
            )
            await self._session.execute(
                delete(TraceNode).where(TraceNode.id.in_(node_ids))
            )
            await self._session.flush()

        stats = {"nodes": 0, "edges": 0}

        # REQUIREMENT ← understanding known_requirements
        req_nodes: list[tuple[TraceNode, str]] = []
        from project_runtime.understanding.models import ProjectUnderstanding

        stmt = (
            select(ProjectUnderstanding)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        und = (await self._session.execute(stmt)).scalar_one_or_none()
        if und is not None and und.known_requirements_json:
            for i, req in enumerate(json.loads(und.known_requirements_json)):
                node = await self.add_node(
                    project_id, TraceKind.REQUIREMENT,
                    f"understanding:{und.id}#req-{i}",
                    req[:120],
                )
                req_nodes.append((node, req))
                stats["nodes"] += 1

        # ARCHITECTURE ← ProcedureNode workstream（playbook 来源）
        arch_nodes: dict[str, TraceNode] = {}
        from project_runtime.project_state.models import ProcedureNode

        pn_stmt = select(ProcedureNode).where(
            ProcedureNode.model_id.in_(
                select(
                    __import__(
                        "project_runtime.project_state.models",
                        fromlist=["ProjectProcedureModel"],
                    ).ProjectProcedureModel.id
                ).where(
                    __import__(
                        "project_runtime.project_state.models",
                        fromlist=["ProjectProcedureModel"],
                    ).ProjectProcedureModel.project_id == project_id
                )
            )
        )
        for pn in (await self._session.execute(pn_stmt)).scalars().all():
            if pn.workstream and pn.workstream not in arch_nodes:
                arch_nodes[pn.workstream] = await self.add_node(
                    project_id, TraceKind.ARCHITECTURE,
                    f"workstream:{pn.workstream}", pn.workstream,
                )
                stats["nodes"] += 1

        # TASK + ARTIFACT/TEST + GATE
        tasks = (
            (
                await self._session.execute(
                    select(Task).where(Task.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        artifacts = (
            (
                await self._session.execute(
                    select(Artifact).where(Artifact.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        gates = (
            (
                await self._session.execute(
                    select(GateResult).where(
                        GateResult.task_id.in_([t.id for t in tasks])
                    )
                )
            )
            .scalars()
            .all()
        ) if tasks else []

        task_nodes: dict[int, TraceNode] = {}
        for t in tasks:
            node = await self.add_node(
                project_id, TraceKind.TASK, f"task:{t.id}", t.name
            )
            task_nodes[t.id] = node
            stats["nodes"] += 1
            # task → workstream（ARCHITECTURE 上游）
            ref = json.loads(t.playbook_ref or "{}")
            ws = ref.get("workstream_name")
            if ws and ws in arch_nodes:
                await self.add_edge(
                    project_id, arch_nodes[ws], node, TraceEdgeRel.DERIVES
                )
                stats["edges"] += 1

        # artifact / test
        from project_runtime.project_state.enums import ArtifactType

        for a in artifacts:
            kind = (
                TraceKind.TEST
                if a.artifact_type == ArtifactType.TEST_RESULT
                else TraceKind.ARTIFACT
            )
            anode = await self.add_node(
                project_id, kind, f"artifact:{a.id}", a.name
            )
            stats["nodes"] += 1
            if a.task_id and a.task_id in task_nodes:
                await self.add_edge(
                    project_id, task_nodes[a.task_id], anode,
                    TraceEdgeRel.PRODUCES,
                )
                stats["edges"] += 1

        # gate
        for g in gates:
            gnode = await self.add_node(
                project_id, TraceKind.GATE, f"gate:{g.id}", g.gate_name
            )
            stats["nodes"] += 1
            if g.task_id in task_nodes:
                await self.add_edge(
                    project_id, task_nodes[g.task_id], gnode,
                    TraceEdgeRel.GATED_BY,
                )
                stats["edges"] += 1

        # requirement → task（V0.1 名称关联：requirement 文本含 task 名或反之）
        for req_node, req_text in req_nodes:
            for t in tasks:
                if t.name and t.name[:6] in req_text:
                    await self.add_edge(
                        project_id, req_node, task_nodes[t.id],
                        TraceEdgeRel.DERIVES,
                    )
                    stats["edges"] += 1
                    break

        # DELIVERY ← DeliveryItem；artifact → delivery 名称匹配
        from project_runtime.project_state.enums import DeliveryItemStatus

        ditems = (
            (
                await self._session.execute(
                    select(DeliveryItem).where(
                        DeliveryItem.status != DeliveryItemStatus.PENDING,
                        DeliveryItem.requirement_ref.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for d in ditems:
            dnode = await self.add_node(
                project_id, TraceKind.DELIVERY,
                f"delivery_item:{d.id}", d.requirement_ref[:120],
            )
            stats["nodes"] += 1
            def _bigrams(x: str) -> set[str]:
                return {x[i:i + 2] for i in range(len(x) - 1)} if len(x) > 1 else {x}

            for a in artifacts:
                # V0.1 名称关联: 2-gram 交集非空即关联（中文友好）
                an, dn = a.name or "", d.requirement_ref
                if an and _bigrams(an) & _bigrams(dn):
                    await self.add_edge(
                        project_id,
                        await self._find_node(
                            project_id,
                            TraceKind.TEST
                            if a.artifact_type == ArtifactType.TEST_RESULT
                            else TraceKind.ARTIFACT,
                            f"artifact:{a.id}",
                        ) or dnode,  # fall back safe
                        dnode, TraceEdgeRel.DELIVERS,
                    )
                    stats["edges"] += 1
                    break

        logger.info(
            "Traceability rebuilt: project=%d nodes=%d edges=%d",
            project_id, stats["nodes"], stats["edges"],
        )
        return stats

    # ── helpers ─────────────────────────────────────────────────────

    async def _find_node(
        self, project_id: int, kind: TraceKind, ref_id: str
    ) -> TraceNode | None:
        stmt = select(TraceNode).where(
            TraceNode.project_id == project_id,
            TraceNode.kind == kind,
            TraceNode.ref_id == ref_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _nodes_of(
        self, project_id: int, kind: TraceKind
    ) -> list[TraceNode]:
        stmt = select(TraceNode).where(
            TraceNode.project_id == project_id, TraceNode.kind == kind
        )
        return list((await self._session.execute(stmt)).scalars().all())
