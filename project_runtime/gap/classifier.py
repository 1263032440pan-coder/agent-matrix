"""GapClassifier — classify raw MethodReadiness gaps into structured MethodGap records.

Routes each gap to the appropriate GapType and resolution strategy.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import GapType
from project_runtime.project_state.models import (
    MethodGap,
    ProjectReadinessAssessment,
)

# ── Dimension → GapType mapping (D08 — semantic, not fixed) ───────────────

_DIMENSION_TO_GAP_TYPE: dict[str, GapType] = {
    "scope_coverage": GapType.PROCESS,
    "action_explicitness": GapType.EXPLICITNESS,
    "artifact_definition": GapType.EXPLICITNESS,
    "acceptance_definition": GapType.EXPLICITNESS,
    "dependency_transition": GapType.PROCESS,
    "constraint_authority": GapType.PROCESS,
}

# ── GapType → resolution strategy ────────────────────────────────────────

_RESOLUTION_STRATEGIES: dict[GapType, str] = {
    GapType.PRIVATE: "USER_CLARIFICATION",
    GapType.EXPLICITNESS: "MODEL_PROPOSAL",
    GapType.DOMAIN: "DOMAIN_RESEARCH",
    GapType.TECHNICAL: "TECHNICAL_RESEARCH",
    GapType.PROCESS: "PROJECT_METHOD_RESEARCH",
    GapType.CAPABILITY: "PROVIDER_RESOLUTION",
}

# Severity heuristic: MISSING dimensions → HIGH, PARTIAL → MEDIUM
_SEVERITY_FROM_STATUS = {"MISSING": "HIGH", "PARTIAL": "MEDIUM"}


class GapClassifier:
    """Takes a ProjectReadinessAssessment and produces structured MethodGap records."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def classify(self, assessment_id: int) -> list[MethodGap]:
        """Idempotent (D07-TD01 batch-1): repeated calls on the same assessment
        return the existing gaps instead of duplicating them."""
        assessment = await self._get_assessment(assessment_id)
        if assessment is None:
            return []

        existing = await self.list_gaps(assessment_id)
        if existing:
            return existing

        gaps_data = json.loads(assessment.gaps or "{}")
        raw_gaps: list[dict] = gaps_data.get("method_gaps", [])

        method_gaps: list[MethodGap] = []
        for raw in raw_gaps:
            gap_type = self._classify_gap_type(raw)
            severity = self._classify_severity(raw)
            strategy = _RESOLUTION_STRATEGIES[gap_type]

            mg = MethodGap(
                assessment_id=assessment_id,
                gap_type=gap_type,
                scope_ref=raw.get("source_ref", ""),
                description=raw.get("reason", ""),
                severity=severity,
                resolution_strategy=strategy,
                source_refs=json.dumps(
                    {
                        "node_id": raw.get("node_id"),
                        "dimension": raw.get("dimension"),
                    }
                ),
            )
            self._session.add(mg)
            method_gaps.append(mg)

        await self._session.flush()
        return method_gaps

    async def list_gaps(self, assessment_id: int) -> list[MethodGap]:
        stmt = select(MethodGap).where(MethodGap.assessment_id == assessment_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    def _classify_gap_type(self, raw_gap: dict) -> GapType:
        dim = raw_gap.get("dimension", "")
        return _DIMENSION_TO_GAP_TYPE.get(dim, GapType.EXPLICITNESS)

    def _classify_severity(self, raw_gap: dict) -> str:
        return _SEVERITY_FROM_STATUS.get(
            raw_gap.get("dimension_status", "PARTIAL"), "MEDIUM"
        )

    async def _get_assessment(self, assessment_id: int) -> ProjectReadinessAssessment | None:
        stmt = select(ProjectReadinessAssessment).where(
            ProjectReadinessAssessment.id == assessment_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
