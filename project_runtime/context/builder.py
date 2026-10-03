"""ContextBuilder (WP-010) — 为 Task 构建最小充分 Context Package.

构造顺序（规范）:
    Task → State Memory → Dependency → Artifact Relation → Summary
        → Retrieval Memory → Execution Evidence → Token Budget

优先级:
    P0 当前任务/验收/约束/依赖 —— 永不裁剪（验收红线）
    P1 决策/摘要/直接上游产物
    P2 检索结果/近期证据（检索结果标注非事实——禁止当事实）
    P3 非关键历史 —— 预算不够最先丢

禁止（结构性断言固化）:
    全量加载项目历史 / 直接访问具体 Retrieval 实现 / Retrieval 当事实

Extension: context.providers（构建后补充）/ context.middleware（最终
返回前变换——必须返回新 ContextPackage，不得改 Project State）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.context.package import (
    P0,
    P1,
    P2,
    P3,
    ContextPackage,
    ContextSection,
)
from project_runtime.memory.manager import ProjectMemoryManager

logger = logging.getLogger(__name__)

# 粗粒度 token 估算（中文≈1.6 字/token，英文≈4 字符/token——取保守混合值）
_CHARS_PER_TOKEN = 2.5


def _tokens(text: str) -> int:
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


class ContextBuilder:
    def __init__(
        self,
        session: AsyncSession,
        *,
        budget_tokens: int = 8000,
        retrieval_limit: int = 3,
        history_limit: int = 5,
        executor=None,
    ):
        self._session = session
        self._budget = budget_tokens
        self._retrieval_limit = retrieval_limit
        self._history_limit = history_limit
        self._executor = executor

    async def build(self, project_id: int, task_id: int) -> ContextPackage:
        """为单个 Task 构建 Context Package（最小充分，非全量）。"""
        sections: list[ContextSection] = []
        seen_content: set[str] = set()  # 内容去重（验收 §3）

        def add(priority: int, kind: str, content: str, source_ref: str) -> None:
            key = content.strip()
            if not key or key in seen_content:
                return
            seen_content.add(key)
            sections.append(ContextSection(
                priority=priority, kind=kind, content=content,
                source_ref=source_ref, tokens=_tokens(content),
            ))

        # ── P0: 当前任务本体（State Memory）──
        from project_runtime.project_state.models import Task

        task = await self._session.get(Task, task_id)
        if task is None or task.project_id != project_id:
            raise ValueError(f"Task {task_id} not in project {project_id}")

        add(P0, "task", f"当前任务：{task.name}", f"task:{task_id}")

        # 上游 ProcedureNode 的验收/约束（方法上下文——P0 不裁剪）
        node = await self._procedure_node_of(task)
        if node is not None:
            if node.acceptance_criteria and node.acceptance_criteria != "UNKNOWN":
                add(P0, "acceptance",
                    f"验收标准：{node.acceptance_criteria}",
                    f"procedure_node:{node.id}")
            if node.constraints and node.constraints != "UNKNOWN":
                add(P0, "constraint",
                    f"约束：{node.constraints}",
                    f"procedure_node:{node.id}")
            if node.activities and node.activities != "UNKNOWN":
                add(P0, "task",
                    f"活动说明：{node.activities}",
                    f"procedure_node:{node.id}")

        # 依赖（P0）
        from project_runtime.project_state.models import TaskDependency

        deps = (
            (
                await self._session.execute(
                    select(TaskDependency).where(
                        TaskDependency.task_id == task_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if deps:
            dep_names = []
            for d in deps:
                up = await self._session.get(Task, d.depends_on_id)
                dep_names.append(
                    f"{up.name}（{up.status.value}）" if up else f"task:{d.depends_on_id}"
                )
            add(P0, "dependency",
                f"上游依赖（须 ACCEPTED 才解锁）：{'；'.join(dep_names)}",
                f"task:{task_id}:deps")

        # ── P1: 摘要 + 直接上游产物 + 决策 ──
        mgr = ProjectMemoryManager(engine=self._session.bind)

        summaries = await mgr.get_summary_memory(project_id)
        for smp in summaries[:3]:
            add(P1, "summary", smp.get("summary", ""),
                f"summary:{smp.get('scope_type', '?')}")

        upstream_ids = {d.depends_on_id for d in deps}
        artifacts = await mgr.get_artifact_memory(project_id)
        direct_up = [a for a in artifacts if a.get("task_id") in upstream_ids]
        for a in direct_up[:3]:
            add(P1, "artifact",
                f"上游产物「{a['name']}」v{a['version']}（sha {str(a['sha256'])[:12]}）",
                f"artifact:{a['artifact_id']}")

        # ── P2: 检索（经 MemoryManager.retrieve——统一门面）+ 近期证据 ──
        query = f"{task.name} {node.acceptance_criteria if node else ''}".strip()
        retrieval = await mgr.retrieve(
            query, project_id=project_id, limit=self._retrieval_limit
        )
        for hit in (retrieval.get("results") or [])[: self._retrieval_limit]:
            content = hit.get("content") or hit.get("excerpt") or ""
            if content:
                # 检索结果标注非事实（禁止当事实——验收红线）
                add(P2, "retrieval",
                    f"[检索参考·非事实] {content[:300]}",
                    f"retrieval:{hit.get('source', '?')}:{hit.get('source_id', '?')}")

        events = await mgr.get_execution_memory(project_id)
        for ev in (events.get("events") or [])[: self._history_limit]:
            msg = ev.get("message", "")
            if msg:
                add(P2, "evidence", f"[近期] {msg[:200]}", f"event:{ev.get('id', '?')}")

        # ── P3: 非关键历史（其余任务状态摘要行）──
        state = await mgr.get_state_memory(project_id)
        other_tasks = [
            t for t in state["tasks"]
            if t["id"] != task_id and t["id"] not in upstream_ids
        ][: self._history_limit]
        if other_tasks:
            add(P3, "history",
                "项目其他任务（非关键历史）：" + "；".join(
                    f"{t['name']}={t['status']}" for t in other_tasks
                ),
                f"project:{project_id}:tasks")

        # ── extension: context.providers（构建后补充，advisory）──
        if self._executor is not None:
            try:
                await self._executor.fire_hook(
                    "context.providers",
                    {"project_id": project_id, "task_id": task_id},
                )
            except Exception:  # noqa: BLE001
                logger.debug("context.providers hook failed", exc_info=True)

        # ── Token Budget: P3 先丢、P2 降级截断，P0/P1 保住 ──
        pkg = ContextPackage(
            project_id=project_id, task_id=task_id,
            sections=tuple(sections), budget_tokens=self._budget,
        )
        pkg = self._apply_budget(pkg)

        # ── extension: context.middleware（变换必须产新对象）──
        if self._executor is not None:
            try:
                outcome = await self._executor.fire_hook(
                    "context.middleware",
                    {"project_id": project_id, "task_id": task_id,
                     "package": pkg},
                )
                new_pkg = self._extract_middleware_package(outcome, pkg)
                if new_pkg is not None:
                    pkg = new_pkg
            except Exception:  # noqa: BLE001
                logger.debug("context.middleware hook failed", exc_info=True)

        return pkg

    # ── 预算实施: P0 不裁剪（验收红线）─────────────────────────────

    def _apply_budget(self, pkg: ContextPackage) -> ContextPackage:
        if pkg.total_tokens <= self._budget:
            return pkg

        # P0 永不裁剪（验收红线）。丢弃顺序: P3 → P2 → P1 非方法段
        # （summary/history 类；上游产物 artifact 与 acceptance 线索最后丢）。
        # P1 内部丢弃序: history < summary < artifact（证据链优先保）。
        p1_drop_order = {"history": 0, "summary": 1, "artifact": 2}
        kept: list[ContextSection] = [s for s in pkg.sections if s.priority == P0]
        budget_left = self._budget - sum(s.tokens for s in kept)
        dropped: list[str] = []

        # 评估顺序 = 优先级从低到高（P3 先评估、P1 最后；P0 不进列表）
        for s in sorted(
            (x for x in pkg.sections if x.priority != P0),
            key=lambda x: (-x.priority, -(p1_drop_order.get(x.kind, 1)
                          if x.priority == P1 else 0)),
        ):
            if budget_left - s.tokens >= 0:
                kept.append(s)
                budget_left -= s.tokens
            else:
                dropped.append(s.kind)

        truncated = bool(dropped) or (
            sum(s.tokens for s in kept) >= self._budget
        )
        return ContextPackage(
            project_id=pkg.project_id, task_id=pkg.task_id,
            sections=tuple(kept), budget_tokens=self._budget,
            truncated=truncated, dropped_kinds=tuple(dropped),
        )

    # ── helpers ─────────────────────────────────────────────────────

    async def _procedure_node_of(self, task):
        """Task 名 → 最新 ProcedureModel 里的同名节点（V0.1 名称匹配）。"""
        from project_runtime.project_state.models import (
            ProcedureNode,
            ProjectProcedureModel,
        )

        stmt = (
            select(ProcedureNode)
            .join(
                ProjectProcedureModel,
                ProcedureNode.model_id == ProjectProcedureModel.id,
            )
            .where(
                ProjectProcedureModel.project_id == task.project_id,
                ProcedureNode.name == task.name,
            )
            .order_by(ProcedureNode.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _extract_middleware_package(outcome, fallback: ContextPackage):
        """Middleware 结果归一：必须产新 ContextPackage（验收红线——
        原对象 frozen，原地改在类型层不可能；非 ContextPackage 返回
        一律忽略保 fallback）。"""
        results = getattr(outcome, "results", None) or []
        for r in results:
            value = getattr(r, "value", None)
            if isinstance(value, dict):
                pkg = value.get("package")
                if isinstance(pkg, ContextPackage) and pkg is not fallback:
                    return pkg
        return None
