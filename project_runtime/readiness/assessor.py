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
        gaps = self._build_gaps(results)
        summary = self._build_summary(results)
        assessment = await self._create_assessment(project_id, model_id, results, gaps, summary)

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

    def _build_gaps(self, results: list[dict]) -> list[dict]:
        """Generate structured method gaps from dimension analysis (Plan A — V0.1)."""
        gaps: list[dict] = []
        for node in results:
            for dim_name, dim_data in node["dimensions"].items():
                if dim_data["status"] in ("MISSING", "PARTIAL"):
                    # Determine gap type via simplified dimension → type mapping
                    gap_type = self._dimension_to_gap_type(dim_name)
                    # Collect fields that are UNKNOWN
                    missing_fields = [
                        f for f, v in dim_data["fields"].items() if v == "UNKNOWN"
                    ]
                    gaps.append({
                        "node_id": node["node_id"],
                        "node_name": node["node_name"],
                        "dimension": dim_name,
                        "dimension_status": dim_data["status"],
                        "gap_type": gap_type,
                        "reason": (
                            f"Dimension '{dim_name}' is {dim_data['status']}: "
                            f"{', '.join(missing_fields) if missing_fields else 'field undefined'}"
                        ),
                        "source_ref": f"procedure_node:{node['node_id']}",
                    })
        return gaps

    @staticmethod
    def _dimension_to_gap_type(dim_name: str) -> str:
        """Simplified dimension → gap type mapping for V0.1."""
        return {
            "scope_coverage": "EXPLICITNESS",
            "action_explicitness": "EXPLICITNESS",
            "artifact_definition": "EXPLICITNESS",
            "acceptance_definition": "EXPLICITNESS",
            "dependency_transition": "PROCESS",
            "constraint_authority": "PROCESS",
        }.get(dim_name, "EXPLICITNESS")

    async def _create_assessment(
        self, project_id: int, model_id: int, results: list[dict],
        gaps: list[dict] | None = None, summary: dict | None = None,
    ) -> ProjectReadinessAssessment:
        assessment = ProjectReadinessAssessment(
            project_id=project_id,
            model_id=model_id,
            method_readiness=json.dumps(results),
            execution_capability_readiness=None,  # D08
            gaps=json.dumps({"method_gaps": gaps or [], "execution_gaps": []}),
            coverage_summary=json.dumps(summary) if summary else None,
        )
        self._session.add(assessment)
        await self._session.flush()
        return assessment

    async def _get_nodes(self, model_id: int) -> list[ProcedureNode]:
        stmt = select(ProcedureNode).where(ProcedureNode.model_id == model_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
