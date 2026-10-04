"""Replanner trigger sources (WP-015 / 10-规范).

四触发源:
  GATE_FAIL        Gate FAIL / REWORK 判定
  FACT_CHANGE      项目事实变化（方法资产指纹变化——新资产/改资产）
  RESEARCH_UPDATE  Research 记录转 ANSWERED（新知识可用）
  USER_CHANGE      用户需求变化（understanding 快照 goal/requirements diff）
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import GateStatus
from project_runtime.project_state.models import (
    GateResult,
    MethodAsset,
)


class ReplanTrigger(str, enum.Enum):
    GATE_FAIL = "GATE_FAIL"
    FACT_CHANGE = "FACT_CHANGE"
    RESEARCH_UPDATE = "RESEARCH_UPDATE"
    USER_CHANGE = "USER_CHANGE"


@dataclass
class TriggerReport:
    trigger: ReplanTrigger
    detail: str
    affected_evidence: list[str] = field(default_factory=list)


class TriggerDetector:
    """检测项目是否需要重规划 + 原因定位（不执行重规划）。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def detect(self, project_id: int, *, baseline: dict | None = None) -> list[TriggerReport]:
        """baseline: 上次规划时的状态快照（asset 指纹集 + research ids +
        understanding 快照 id）。None → 首次无基线（不触发）。"""
        if baseline is None:
            return []
        reports: list[TriggerReport] = []

        # 1. Gate FAIL（REWORK 落定）
        gate_fail = await self._gate_fail(project_id)
        if gate_fail is not None:
            reports.append(gate_fail)

        # 2. 事实变化: 资产指纹集 vs baseline
        reports.extend(await self._fact_change(project_id, baseline))

        # 3. Research 更新: ANSWERED 记录 id 集新增
        reports.extend(await self._research_update(project_id, baseline))

        # 4. 用户需求变化: understanding 快照 id 前进
        reports.extend(await self._user_change(project_id, baseline))

        return reports

    async def _gate_fail(self, project_id: int) -> TriggerReport | None:
        from project_runtime.project_state.models import Task

        stmt = (
            select(GateResult)
            .join(Task, GateResult.task_id == Task.id)
            .where(Task.project_id == project_id)
            .order_by(GateResult.id.desc())
            .limit(1)
        )
        latest = (await self._session.execute(stmt)).scalar_one_or_none()
        if latest is not None and latest.status in (
            GateStatus.FAIL, GateStatus.NEED_APPROVAL,
        ):
            return TriggerReport(
                trigger=ReplanTrigger.GATE_FAIL,
                detail=f"最新 Gate {latest.gate_name} = {latest.status.value}",
                affected_evidence=[f"gate_result:{latest.id}"],
            )
        return None

    async def _fact_change(
        self, project_id: int, baseline: dict
    ) -> list[TriggerReport]:
        stmt = select(MethodAsset).where(
            MethodAsset.project_id == project_id
        )
        assets = (await self._session.execute(stmt)).scalars().all()
        current = {a.content_hash for a in assets}
        known = set(baseline.get("asset_hashes") or [])
        new_hashes = current - known
        if new_hashes:
            return [TriggerReport(
                trigger=ReplanTrigger.FACT_CHANGE,
                detail=f"方法资产变化：{len(new_hashes)} 个新增/变更",
                affected_evidence=[
                    f"method_asset:{a.id}"
                    for a in assets if a.content_hash in new_hashes
                ],
            )]
        return []

    async def _research_update(
        self, project_id: int, baseline: dict
    ) -> list[TriggerReport]:
        from project_runtime.research.models import ResearchRecord

        stmt = select(ResearchRecord).where(
            ResearchRecord.project_id == project_id,
            ResearchRecord.status == "ANSWERED",
        )
        records = (await self._session.execute(stmt)).scalars().all()
        current = {r.id for r in records}
        known = set(baseline.get("answered_research_ids") or [])
        new_ids = current - known
        if new_ids:
            return [TriggerReport(
                trigger=ReplanTrigger.RESEARCH_UPDATE,
                detail=f"研究完成更新：{len(new_ids)} 条新 ANSWERED",
                affected_evidence=[
                    f"research:{i}" for i in sorted(new_ids)
                ],
            )]
        return []

    async def _user_change(
        self, project_id: int, baseline: dict
    ) -> list[TriggerReport]:
        from project_runtime.understanding.models import ProjectUnderstanding

        stmt = (
            select(ProjectUnderstanding.id)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        latest_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if latest_id is not None and latest_id != baseline.get(
            "understanding_id"
        ):
            return [TriggerReport(
                trigger=ReplanTrigger.USER_CHANGE,
                detail=f"理解快照前进：→ {latest_id}",
                affected_evidence=[f"understanding:{latest_id}"],
            )]
        return []


def capture_baseline(
    project_id: int,
    asset_hashes: list[str],
    answered_research_ids: list[int],
    understanding_id: int | None,
) -> dict:
    """规划完成时的基线快照（供下次 detect 对比）。"""
    return {
        "project_id": project_id,
        "asset_hashes": sorted(asset_hashes),
        "answered_research_ids": sorted(answered_research_ids),
        "understanding_id": understanding_id,
    }
