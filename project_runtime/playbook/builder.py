"""PlaybookBuilder — synthesise D05-D08 outputs into a ProjectPlaybook.

The Playbook is the single source of truth for the Dynamic Planner (D10).
Every workstream carries provenance (source + authority).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import (
    MethodAsset,
    MethodGap,
    ProcedureNode,
    ProjectPlaybook,
    ProjectProcedureModel,
    ProjectReadinessAssessment,
)


class PlaybookBuilder:
    """Build a ProjectPlaybook from existing project artifacts."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def build(self, project_id: int) -> ProjectPlaybook:
        """Synthesise D05–D08 outputs into a Project Playbook."""
        assets = await self._get_assets(project_id)
        procedure_model = await self._get_latest_procedure_model(project_id)
        assessment = await self._get_latest_assessment(project_id)
        gaps = await self._get_gaps(assessment)

        # ── workstreams ──────────────────────────────────────────────────
        nodes = await self._get_nodes(procedure_model)
        workstreams = self._build_workstreams(nodes, assets, gaps)

        # ── provenance_map ───────────────────────────────────────────────
        provenance_map = self._build_provenance_map(assets, nodes)

        # ── gap references ───────────────────────────────────────────────
        gap_refs = [
            {"gap_id": g.id, "gap_type": g.gap_type.value, "description": g.description}
            for g in gaps
        ]
        unresolved = [g for g in gaps if g.status == "OPEN"]

        playbook = ProjectPlaybook(
            project_id=project_id,
            method_asset_refs=json.dumps(
                [{"id": a.id, "type": a.asset_type.value, "source": a.source_path} for a in assets]
            ),
            procedure_model_version=(
                json.dumps(
                    {"model_id": procedure_model.id, "version": procedure_model.version}
                )
                if procedure_model
                else None
            ),
            method_readiness_snapshot=assessment.coverage_summary if assessment else None,
            execution_readiness_snapshot=None,  # D11
            provenance_map=json.dumps(provenance_map),
            gap_refs=json.dumps(gap_refs),
            resolved_gap_refs=json.dumps([]),
            unresolved_gaps=json.dumps(
                [{"gap_id": g.id, "gap_type": g.gap_type.value} for g in unresolved]
            ),
            workstreams=json.dumps(workstreams),
        )
        self._session.add(playbook)
        await self._session.flush()
        return playbook

    # ── workstream construction ──────────────────────────────────────────

    def _build_workstreams(
        self, nodes: list[ProcedureNode], assets: list[MethodAsset], gaps: list[MethodGap]
    ) -> list[dict]:
        workstreams: list[dict] = []
        gap_by_node: dict[int, list[MethodGap]] = {}
        for g in gaps:
            refs = json.loads(g.source_refs or "{}")
            node_id = refs.get("node_id")
            if node_id:
                gap_by_node.setdefault(node_id, []).append(g)

        for node in nodes:
            asset = next(
                (a for a in assets if a.source_path and a.source_path in (node.source_ref or "")),
                None,
            )
            source_type = self._determine_source_type(asset)
            authority = asset.authority.value if asset and asset.authority else "PROPOSED"

            ws = {
                "name": node.name,
                "workstream": node.workstream or "default",
                "activities": node.activities,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "roles": node.roles,
                "dependencies": node.dependencies,
                "gate": node.gate,
                "acceptance_criteria": node.acceptance_criteria,
                "constraints": node.constraints,
                "source": {
                    "type": source_type,
                    "authority": authority,
                    "asset_id": asset.id if asset else None,
                    "procedure_node_id": node.id,
                },
                "gaps": [
                    {"gap_id": g.id, "gap_type": g.gap_type.value, "severity": g.severity}
                    for g in gap_by_node.get(node.id, [])
                ],
            }
            workstreams.append(ws)

        return workstreams

    def _determine_source_type(self, asset: MethodAsset | None) -> str:
        """Determine source type based on asset origin, not authority level.

        V0.1: all imported assets are USER_METHOD. Authority is tracked
        separately in workstream.source.authority for Planner to respect.
        """
        if asset is None:
            return "MODEL_INFERENCE"
        # V0.1: user-imported assets are always USER_METHOD
        return "USER_METHOD"

    # ── provenance map ───────────────────────────────────────────────────

    def _build_provenance_map(
        self, assets: list[MethodAsset], nodes: list[ProcedureNode]
    ) -> dict:
        return {
            "assets": [
                {
                    "asset_id": a.id,
                    "asset_type": a.asset_type.value,
                    "authority": a.authority.value if a.authority else "PROPOSED",
                    "source_path": a.source_path,
                }
                for a in assets
            ],
            "procedure_nodes": len(nodes),
            "model_inference": "V0.1 KeywordExtractor — LLM extraction not applied",
        }

    # ── data access helpers ──────────────────────────────────────────────

    async def _get_assets(self, project_id: int) -> list[MethodAsset]:
        stmt = select(MethodAsset).where(MethodAsset.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_latest_procedure_model(self, project_id: int) -> ProjectProcedureModel | None:
        stmt = (
            select(ProjectProcedureModel)
            .where(ProjectProcedureModel.project_id == project_id)
            .order_by(ProjectProcedureModel.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_latest_assessment(self, project_id: int) -> ProjectReadinessAssessment | None:
        stmt = (
            select(ProjectReadinessAssessment)
            .where(ProjectReadinessAssessment.project_id == project_id)
            .order_by(ProjectReadinessAssessment.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_nodes(self, model: ProjectProcedureModel | None) -> list[ProcedureNode]:
        if model is None:
            return []
        stmt = select(ProcedureNode).where(ProcedureNode.model_id == model.id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_gaps(self, assessment: ProjectReadinessAssessment | None) -> list[MethodGap]:
        if assessment is None:
            return []
        stmt = select(MethodGap).where(MethodGap.assessment_id == assessment.id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
