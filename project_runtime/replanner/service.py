"""Replanner service (WP-015).

主链: Trigger → Impact Analysis → Capability Re-assessment →
      Need Research? → Playbook Update（新版本）→ Planning Context Update
      （快照式）→ Replan（DynamicPlanner 幂等）

红线（验收 §3）: 不直接篡改已确认需求——MANDATORY 资产的 authority 与
内容永不改写；replan 只产生新版本（新 model/playbook）与新 Task，
已完成 Task（ACCEPTED）不动。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.planner.planner import DynamicPlanner
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.project_state.models import MethodAsset, Task
from project_runtime.replanner.triggers import (
    TriggerDetector,
    TriggerReport,
    capture_baseline,
)
from project_runtime.research.router import GapResolver

logger = logging.getLogger(__name__)


class ReplanOutcome:
    """一次重规划的结果（可审计）。"""

    def __init__(self):
        self.triggered: list[TriggerReport] = []
        self.affected_workstreams: list[str] = []
        self.research_created: list[int] = []
        self.playbook_id: int | None = None
        self.playbook_is_new: bool = False
        self.tasks_created: int = 0
        self.baseline: dict | None = None
        self.skipped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "triggered": [
                {"trigger": t.trigger.value, "detail": t.detail}
                for t in self.triggered
            ],
            "affected_workstreams": self.affected_workstreams,
            "research_created": self.research_created,
            "playbook_id": self.playbook_id,
            "playbook_is_new": self.playbook_is_new,
            "tasks_created": self.tasks_created,
        }


class Replanner:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def replan(
        self, project_id: int, *, baseline: dict | None = None
    ) -> ReplanOutcome:
        """检测触发 → 走完重规划主链。无触发返回 skipped。"""
        outcome = ReplanOutcome()

        # 1. Trigger detection
        reports = await TriggerDetector(self._session).detect(
            project_id, baseline=baseline
        )
        if not reports:
            outcome.skipped_reason = "no trigger detected"
            return outcome
        outcome.triggered = reports

        # 2. Capability re-assessment: 新/变更材料 → 重 normalize →
        #    新 gaps（含受影响 node 追溯基础）
        #    新/变更材料 → 重 normalize → 新 gaps → WP-029 路由建研究
        from project_runtime.procedure import ProcedureNormalizer
        from project_runtime.readiness.assessor import MethodReadinessAssessor

        model = await ProcedureNormalizer(self._session).normalize(project_id)
        await MethodReadinessAssessor(self._session).assess(
            project_id, model.id
        )

        # 3. Impact analysis: normalize 之后跑（新资产的 ProcedureNode
        #    provenance 可追溯）+ 需要研究时路由
        outcome.affected_workstreams = await self._impact_analysis(
            project_id, reports
        )
        outcome.research_created = await self._route_new_gaps(project_id)

        # 4. Playbook update（新版本——Builder 按 (project, model) 幂等：
        #    新 model 产新 playbook，旧 playbook 历史保留不覆盖）
        prior_ids = await self._playbook_ids(project_id)
        playbook = await PlaybookBuilder(self._session).build(project_id)
        outcome.playbook_id = playbook.id
        outcome.playbook_is_new = playbook.id not in prior_ids

        # 5. Replan: DynamicPlanner 幂等（同名 Task 复用；变化产新 Task）
        before_ids = await self._task_ids(project_id)
        planner = DynamicPlanner(self._session)
        await planner.plan(playbook)
        after_ids = await self._task_ids(project_id)
        outcome.tasks_created = len(after_ids) - len(before_ids)
        # pending research requests surface for WP-029 consumption
        for req in getattr(planner, "pending_requests", []):
            logger.info("replan knowledge request: %s", req)

        # 6. New baseline for next detect
        asset_hashes, research_ids, und_id = await self._snapshot_inputs(
            project_id
        )
        outcome.baseline = capture_baseline(
            project_id, asset_hashes, research_ids, und_id
        )
        return outcome

    # ── impact analysis ─────────────────────────────────────────────

    async def _impact_analysis(
        self, project_id: int, reports: list[TriggerReport]
    ) -> list[str]:
        """触发证据 → 受影响 workstream 名（V0.1: 变更资产对应的
        ProcedureNode 名 + Gate FAIL 任务名）。"""
        affected: list[str] = []
        asset_ids = set()
        for r in reports:
            for ev in r.affected_evidence:
                if ev.startswith("method_asset:"):
                    asset_ids.add(int(ev.split(":", 1)[1]))
                elif ev.startswith("gate_result:"):
                    from project_runtime.project_state.models import (
                        GateResult,
                    )

                    gr = await self._session.get(
                        GateResult, int(ev.split(":", 1)[1])
                    )
                    if gr is not None:
                        task = await self._session.get(Task, gr.task_id)
                        if task is not None:
                            affected.append(task.name)

        if asset_ids:
            from project_runtime.project_state.models import ProcedureNode

            stmt = select(ProcedureNode)
            nodes = (await self._session.execute(stmt)).scalars().all()
            for n in nodes:
                try:
                    prov = json.loads(n.provenance or "{}")
                except ValueError:
                    continue
                if prov.get("asset_id") in asset_ids:
                    affected.append(n.name)
        return sorted(set(affected))

    # ── new gaps → research routing (WP-029) ────────────────────────

    async def _route_new_gaps(self, project_id: int) -> list[int]:
        """重评后新产生的 gap → GapResolver 路由；建研究的返回 id。"""
        from project_runtime.gap.classifier import GapClassifier
        from project_runtime.project_state.models import (
            ProjectReadinessAssessment,
        )

        stmt = (
            select(ProjectReadinessAssessment)
            .where(ProjectReadinessAssessment.project_id == project_id)
            .order_by(ProjectReadinessAssessment.id.desc())
            .limit(1)
        )
        assessment = (await self._session.execute(stmt)).scalar_one_or_none()
        if assessment is None:
            return []

        clf = GapClassifier(self._session)
        gaps = await clf.classify(assessment.id)
        created: list[int] = []
        resolver = GapResolver(self._session)
        for g in gaps:
            res = await resolver.resolve_gap(g.id)
            if res.action == "RESEARCH_CREATED" and res.research_id:
                created.append(res.research_id)
        return created

    # ── helpers ─────────────────────────────────────────────────────

    async def _playbook_ids(self, project_id: int) -> set[int]:
        from project_runtime.project_state.models import ProjectPlaybook

        stmt = select(ProjectPlaybook.id).where(
            ProjectPlaybook.project_id == project_id
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def _task_ids(self, project_id: int) -> set[int]:
        stmt = select(Task.id).where(Task.project_id == project_id)
        return set((await self._session.execute(stmt)).scalars().all())

    async def _snapshot_inputs(self, project_id: int):
        stmt = select(MethodAsset.content_hash).where(
            MethodAsset.project_id == project_id
        )
        hashes = list((await self._session.execute(stmt)).scalars().all())

        from project_runtime.research.models import ResearchRecord

        stmt = select(ResearchRecord.id).where(
            ResearchRecord.project_id == project_id,
            ResearchRecord.status == "ANSWERED",
        )
        research_ids = list(
            (await self._session.execute(stmt)).scalars().all()
        )

        from project_runtime.understanding.models import ProjectUnderstanding

        stmt = (
            select(ProjectUnderstanding.id)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        und_id = (await self._session.execute(stmt)).scalar_one_or_none()
        return hashes, research_ids, und_id
