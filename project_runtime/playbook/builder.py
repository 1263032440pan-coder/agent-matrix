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

    def __init__(self, session: AsyncSession, *, executor=None):
        self._session = session
        # WP-030: playbook.build.before/after extension points (advisory)
        self._executor = executor

    async def build(self, project_id: int) -> ProjectPlaybook:
        """Synthesise D05–D08 outputs into a Project Playbook.

        Idempotent (ADR-038): repeated builds for the same (project, latest
        procedure model) return the existing playbook. A NEW model (driven by
        new assets → new normalize → new assessment) produces a NEW playbook —
        evolution is not blocked by idempotency.
        """
        assets = await self._get_assets(project_id)
        procedure_model = await self._get_latest_procedure_model(project_id)
        assessment = await self._get_latest_assessment(project_id)

        existing = await self._get_playbook_for_model(
            project_id, procedure_model.id if procedure_model else None
        )
        if existing is not None:
            return existing

        gaps = await self._get_gaps(assessment)

        # WP-030: extension fire (advisory, never blocks)
        if self._executor is not None:
            try:
                await self._executor.fire_hook(
                    "playbook.build.before",
                    {"project_id": project_id, "assets": len(assets)},
                )
            except Exception:  # noqa: BLE001
                pass

        # WP-030: Research fusion - ANSWERED research enriches workstreams
        # as RECOMMENDED (research NEVER overrides user method; it only ADDS)
        research_refs = await self._get_answered_research(project_id)

        # WP-030: Installed PROJECT_METHOD skills - external optional
        # inputs; recorded as references ONLY, never loaded here
        skill_refs = await self._get_skill_refs(project_id)

        # ── workstreams ──────────────────────────────────────────────────
        nodes = await self._get_nodes(procedure_model)
        workstreams = self._build_workstreams(nodes, assets, gaps)
        for ws in workstreams:
            related = [r for r in research_refs if self._research_relates(r, ws)]
            if related:
                ws["research_enrichments"] = related

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
            execution_readiness_snapshot=(
                assessment.execution_capability_readiness
                if assessment and assessment.execution_capability_readiness
                else None
            ),  # WP-038 dual-axis snapshot wired (D11 placeholder closed)
            provenance_map=json.dumps(provenance_map),
            gap_refs=json.dumps(gap_refs),
            resolved_gap_refs=json.dumps(
                [
                    {"gap_id": g.id, "gap_type": g.gap_type.value}
                    for g in gaps
                    if g.status != "OPEN"
                ]
            ),
            unresolved_gaps=json.dumps(
                [{"gap_id": g.id, "gap_type": g.gap_type.value} for g in unresolved]
            ),
            workstreams=json.dumps(workstreams),
            # WP-030 V1.12: external optional inputs - references only
            project_method_skill_refs=json.dumps(skill_refs) if skill_refs else None,
            task_skill_preferences=json.dumps([]),  # per-task prefs, post-Demo
        )
        self._session.add(playbook)
        await self._session.flush()

        # WP-030: extension fire after
        if self._executor is not None:
            try:
                await self._executor.fire_hook(
                    "playbook.build.after",
                    {"project_id": project_id, "playbook_id": playbook.id},
                )
            except Exception:  # noqa: BLE001
                pass
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

    @staticmethod
    def _determine_source_type(asset: MethodAsset | None) -> str:
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

    # ── WP-030 helpers ─────────────────────────────────────────────────

    async def _get_answered_research(self, project_id: int) -> list[dict]:
        """ANSWERED research records -> RECOMMENDED enrichment refs.

        Research content is NEVER blended into the user method - it lands
        in workstream.research_enrichments with authority=RECOMMENDED
        (question/summary only), for the Planner and humans to inspect.
        """
        from project_runtime.research.models import ResearchRecord

        stmt = select(ResearchRecord).where(
            ResearchRecord.project_id == project_id,
            ResearchRecord.status == "ANSWERED",
        )
        records = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "research_id": r.id,
                "kind": r.research_kind,
                "question": r.question,
                "summary": r.summary,
                "authority": "RECOMMENDED",
                "source": "research",
            }
            for r in records
        ]

    async def _get_skill_refs(self, project_id: int) -> list[dict]:
        """ENABLED PROJECT_METHOD skill bindings -> reference records.

        Records only (skill_id/version/hash/source) - the playbook NEVER
        loads skill content (task-skill loading belongs to the execution
        chain's resolver, not the planning chain).
        """
        try:
            from project_runtime.skills.enums import SkillStatus, SkillType
            from project_runtime.skills.models import (
                SkillBinding,
                SkillDefinition,
            )

            stmt = (
                select(SkillDefinition)
                .join(
                    SkillBinding,
                    SkillBinding.skill_id == SkillDefinition.skill_id,
                )
                .where(
                    SkillBinding.project_id == project_id,
                    SkillDefinition.status == SkillStatus.ENABLED,
                    SkillDefinition.skill_type == SkillType.PROJECT_METHOD,
                )
            )
            skills = (await self._session.execute(stmt)).scalars().all()
            return [
                {
                    "skill_id": sk.skill_id,
                    "version": sk.version,
                    "content_hash": sk.content_hash,
                    "source": "installed-skill",
                    "authority": "OPTIONAL",
                }
                for sk in skills
            ]
        except Exception:  # noqa: BLE001 - skills optional, never blocks
            return []

    @staticmethod
    def _research_relates(r: dict, ws: dict) -> bool:
        """V0.1 relevance: research question mentions the workstream name
        (or vice versa). LLM relevance matching is post-Demo."""
        q = (r.get("question") or "").lower()
        name = (ws.get("name") or "").lower()
        return bool(name) and name in q

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

    async def _get_playbook_for_model(
        self, project_id: int, model_id: int | None
    ) -> ProjectPlaybook | None:
        """Idempotency key: (project_id, latest procedure model id).

        procedure_model_version JSON contains model_id; match on it so a new
        model (new assets → new normalize) yields a NEW playbook while repeat
        builds on the same model return the existing one.
        """
        if model_id is None:
            return None
        stmt = select(ProjectPlaybook).where(
            ProjectPlaybook.project_id == project_id,
            ProjectPlaybook.procedure_model_version.like(f'%"model_id": {model_id}%'),
        )
        return (await self._session.execute(stmt)).scalars().first()

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
