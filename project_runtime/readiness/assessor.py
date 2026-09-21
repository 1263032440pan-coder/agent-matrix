"""MethodReadinessAssessor — evaluate ProcedureNodes across 6 dimensions.

Produces per-node readiness scores and a project-wide readiness snapshot.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import (
    ProcedureNode,
    ProjectReadinessAssessment,
)

UNKNOWN = "UNKNOWN"

# 6 Method Readiness dimensions (per WP-038 § Method Readiness 维度)
_DIMENSIONS = {
    "scope_coverage": ("inputs", "outputs"),
    "action_explicitness": ("activities",),
    "artifact_definition": ("outputs",),
    "acceptance_definition": ("acceptance_criteria",),
    "dependency_transition": ("dependencies",),
    "constraint_authority": ("constraints",),
}


class MethodReadinessAssessor:
    """Evaluate Method Readiness from a ProjectProcedureModel."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def assess(self, project_id: int, model_id: int) -> ProjectReadinessAssessment:
        """Run readiness assessment on all ProcedureNodes in a model."""
        nodes = await self._get_nodes(model_id)
        if not nodes:
            return await self._create_assessment(project_id, model_id, [])

        results: list[dict] = []
        for node in nodes:
            dim_scores = {}
            unknown_count = 0
            for dim_name, fields in _DIMENSIONS.items():
                values = [getattr(node, f, UNKNOWN) for f in fields]
                all_known = all(v != UNKNOWN for v in values)
                any_known = any(v != UNKNOWN for v in values)
                dim_scores[dim_name] = {
                    "status": "COVERED" if all_known else ("PARTIAL" if any_known else "MISSING"),
                    "fields": {f: getattr(node, f, UNKNOWN) for f in fields},
                }
                if not any_known:
                    unknown_count += 1

            # Node-level Method Readiness
            covered = sum(1 for d in dim_scores.values() if d["status"] == "COVERED")
            total = len(_DIMENSIONS)
            node_status = node.readiness_status.value

            results.append(
                {
                    "node_id": node.id,
                    "node_name": node.name,
                    "node_status": node_status,
                    "dimensions": dim_scores,
                    "covered_dimensions": covered,
                    "total_dimensions": total,
                }
            )

        # Project-level summary
        summary = self._build_summary(results)
        assessment = await self._create_assessment(project_id, model_id, results, summary)

        # Store per-node coverage summary
        assessment.coverage_summary = json.dumps(summary)
        await self._session.flush()
        return assessment

    def _build_summary(self, results: list[dict]) -> dict:
        total_nodes = len(results)
        if total_nodes == 0:
            return {"total_nodes": 0, "status": "MISSING"}

        statuses = [r["node_status"] for r in results]
        ready_count = statuses.count("READY")
        partial_count = statuses.count("PARTIAL")
        ambiguous_count = statuses.count("AMBIGUOUS")
        missing_count = statuses.count("MISSING")

        avg_coverage = (
            sum(r["covered_dimensions"] for r in results) / (total_nodes * len(_DIMENSIONS))
            if total_nodes > 0
            else 0
        )

        # Overall project-level readiness
        if ready_count == total_nodes:
            overall = "READY"
        elif missing_count == total_nodes:
            overall = "MISSING"
        elif ambiguous_count > 0:
            overall = "AMBIGUOUS"
        elif partial_count > 0 or ready_count < total_nodes:
            overall = "PARTIAL"
        else:
            overall = "PARTIAL"

        return {
            "total_nodes": total_nodes,
            "overall_status": overall,
            "ready": ready_count,
            "partial": partial_count,
            "ambiguous": ambiguous_count,
            "missing": missing_count,
            "avg_dimension_coverage": round(avg_coverage, 2),
        }

    async def _create_assessment(
        self, project_id: int, model_id: int, results: list[dict], summary: dict | None = None
    ) -> ProjectReadinessAssessment:
        assessment = ProjectReadinessAssessment(
            project_id=project_id,
            model_id=model_id,
            method_readiness=json.dumps(results),
            execution_capability_readiness=None,  # D08
            gaps=json.dumps({"method_gaps": [], "execution_gaps": []}),
            coverage_summary=json.dumps(summary) if summary else None,
        )
        self._session.add(assessment)
        await self._session.flush()
        return assessment

    async def _get_nodes(self, model_id: int) -> list[ProcedureNode]:
        stmt = select(ProcedureNode).where(ProcedureNode.model_id == model_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
