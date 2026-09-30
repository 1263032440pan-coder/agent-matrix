"""Gap Resolution Router (WP-029 / 10-规范).

只对已明确分类的 Gap 进行正确路由：

    PRIVATE     → USER_CLARIFICATION（永不 Web Search——用户私有知识）
    EXPLICITNESS→ MODEL_PROPOSAL / USER_CONFIRM（无需联网，模型可补）
    DOMAIN      → DOMAIN_RESEARCH
    TECHNICAL   → TECHNICAL_RESEARCH
    PROCESS     → PROJECT_METHOD_RESEARCH（仅在方法资产真缺时）
    CAPABILITY  → PROVIDER / PLUGIN / WORKER CAPABILITY RESOLUTION

Research-on-Gap 前置五查（创建新 Research 前必须全部检查）:
    Method Assets / Procedure Model / Project Memory / Installed Skills /
    Existing Research —— 命中任一即引用不建新。

红线:
  - Skill=0 不自动触发 Method Research（方法足够即不联网）
  - Research 不覆盖用户 MANDATORY 流程（authority=MANDATORY 资产上的
    gap 只进 USER_CLARIFICATION / MODEL_PROPOSAL，永不改写流程本身）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import (
    AssetType,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import (
    MethodAsset,
    MethodGap,
)
from project_runtime.research.models import ResearchRecord

logger = logging.getLogger(__name__)

# 六类型路由表（WP-029 §路由）
ROUTE_TABLE: dict[str, str] = {
    "PRIVATE": "USER_CLARIFICATION",
    "EXPLICITNESS": "MODEL_PROPOSAL",
    "DOMAIN": "DOMAIN_RESEARCH",
    "TECHNICAL": "TECHNICAL_RESEARCH",
    "PROCESS": "PROJECT_METHOD_RESEARCH",
    "CAPABILITY": "CAPABILITY_RESOLUTION",
}

# 需要联网的三类（其余永不建 Web Research）
RESEARCH_KINDS = {"DOMAIN", "TECHNICAL", "PROCESS"}


@dataclass
class Resolution:
    """One gap's routing outcome."""

    gap_id: int
    gap_type: str
    route: str                    # ROUTE_TABLE value
    action: str = "RESOLVED_LOCALLY"
    # RESOLVED_LOCALLY / RESEARCH_CREATED / RESEARCH_REUSED /
    # USER_CLARIFICATION_REQUESTED / CAPABILITY_RESOLUTION_REQUESTED
    source: str = ""              # which of the five checks hit
    research_id: int | None = None
    detail: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    """五查结果（研究创建前的证据链）。"""

    method_assets_hit: bool = False
    procedure_model_hit: bool = False
    project_memory_hit: bool = False
    installed_skills_hit: bool = False
    existing_research_hit: bool = False

    @property
    def any_hit(self) -> bool:
        return (
            self.method_assets_hit
            or self.procedure_model_hit
            or self.project_memory_hit
            or self.installed_skills_hit
            or self.existing_research_hit
        )


class GapResolver:
    """Routes classified gaps; creates research ONLY when五查 all miss."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def resolve_gap(self, gap_id: int) -> Resolution:
        gap = await self._session.get(MethodGap, gap_id)
        if gap is None:
            raise ValueError(f"Gap not found: {gap_id}")

        gtype = gap.gap_type.value
        route = ROUTE_TABLE.get(gtype, "USER_CLARIFICATION")

        # ── 红线 1: PRIVATE 永不去 Web Search ─────────────────────────
        if gtype == "PRIVATE":
            return Resolution(
                gap_id=gap_id, gap_type=gtype, route=route,
                action="USER_CLARIFICATION_REQUESTED",
                source="private-gap",
                detail="用户私有知识——只向用户澄清，永不联网检索",
            )

        # ── 红线 2: MANDATORY 资产上的 gap 不改写用户流程 ─────────────
        mandatory = await self._on_mandatory_asset(gap)
        if mandatory and route != "USER_CLARIFICATION":
            return Resolution(
                gap_id=gap_id, gap_type=gtype, route="USER_CLARIFICATION",
                action="USER_CLARIFICATION_REQUESTED",
                source="mandatory-authority",
                detail="Gap 位于 MANDATORY 资产——Research 不覆盖用户强制流程，"
                       "转用户澄清",
            )

        # ── EXPLICITNESS: 模型可补，无需联网 ──────────────────────────
        if gtype == "EXPLICITNESS":
            return Resolution(
                gap_id=gap_id, gap_type=gtype, route=route,
                action="RESOLVED_LOCALLY",
                source="model-proposal",
                detail="明确性缺口——模型补全提案，交用户确认（不联网）",
            )

        # ── CAPABILITY: 能力解析（provider/plugin/worker），非 Research ──
        if gtype == "CAPABILITY":
            return Resolution(
                gap_id=gap_id, gap_type=gtype, route=route,
                action="CAPABILITY_RESOLUTION_REQUESTED",
                source="capability-registry",
                detail="能力缺口走 Provider/Plugin/Worker 注册解析，非 Web Research",
            )

        # ── 需联网的三类: 五查前置 ────────────────────────────────────
        report = await self._five_checks(gap)
        if report.any_hit:
            return Resolution(
                gap_id=gap_id, gap_type=gtype, route=route,
                action="RESOLVED_LOCALLY",
                source=next(
                    n for n, h in (
                        ("method_assets", report.method_assets_hit),
                        ("procedure_model", report.procedure_model_hit),
                        ("project_memory", report.project_memory_hit),
                        ("installed_skills", report.installed_skills_hit),
                        ("existing_research", report.existing_research_hit),
                    ) if h
                ),
                detail="五查命中既有信息——引用不新建 Research",
                notes=[
                    f"method_assets={report.method_assets_hit}",
                    f"procedure_model={report.procedure_model_hit}",
                    f"project_memory={report.project_memory_hit}",
                    f"installed_skills={report.installed_skills_hit}",
                    f"existing_research={report.existing_research_hit}",
                ],
            )

        # 五查全 miss → 建新 Research（幂等: 同 (question, kind) 复用）
        rec = await self._get_or_create_research(gap, gtype, route)
        action = (
            "RESEARCH_REUSED" if rec.status == "ANSWERED" else "RESEARCH_CREATED"
        )
        return Resolution(
            gap_id=gap_id, gap_type=gtype, route=route,
            action=action,
            source="five-checks-missed",
            research_id=rec.id,
            detail="五查未命中——创建/复用 Research 记录",
        )

    # ── 五查实现 ─────────────────────────────────────────────────────

    async def _five_checks(self, gap: MethodGap) -> CheckReport:
        rep = CheckReport()
        scope = (gap.scope_ref or "") + " " + (gap.description or "")

        # 1. Method Assets: 项目已有方法材料（MANDATORY/RECOMMENDED）
        assets = (
            (
                await self._session.execute(
                    select(MethodAsset).where(
                        MethodAsset.project_id == (await self._project_of(gap))
                    )
                )
            )
            .scalars()
            .all()
        )
        rep.method_assets_hit = any(
            a.authority
            in (MethodAssetAuthority.MANDATORY, MethodAssetAuthority.RECOMMENDED)
            for a in assets
        )

        # 2. Procedure Model: 该 gap 指向的节点在模型中维度已有实质值
        # （模型存在≠能回答缺口——检查的是该节点的覆盖，不是模型非空）
        ref = (gap.scope_ref or "").replace("procedure_node:", "")
        if ref.isdigit():
            from project_runtime.project_state.models import ProcedureNode

            node = await self._session.get(ProcedureNode, int(ref))
            if node is not None:
                # the node's substantive fields carry real values (not
                # UNKNOWN placeholders) → the model already covers it
                substantive = [
                    node.inputs, node.outputs, node.activities,
                    node.acceptance_criteria, node.constraints,
                ]
                rep.procedure_model_hit = any(
                    v and v not in ("UNKNOWN", "AMBIGUOUS") for v in substantive
                )

        # 3. Project Memory: 检索命中（走统一门面——WP-025 契约）
        try:
            from project_runtime.memory.manager import ProjectMemoryManager

            mgr = ProjectMemoryManager(engine=self._session.bind)
            hit = await mgr.retrieve(scope[:120], project_id=(await self._project_of(gap)),
                                     limit=3)
            rep.project_memory_hit = bool(hit.get("total"))
        except Exception:  # noqa: BLE001 — memory optional, never blocks
            logger.debug("project memory check skipped", exc_info=True)

        # 4. Installed Skills: ENABLED skill 存在
        try:
            from project_runtime.skills.enums import SkillStatus
            from project_runtime.skills.models import SkillDefinition

            skills = (
                (
                    await self._session.execute(
                        select(SkillDefinition).where(
                            SkillDefinition.status == SkillStatus.ENABLED
                        )
                    )
                )
                .scalars()
                .all()
            )
            rep.installed_skills_hit = len(skills) > 0
        except Exception:  # noqa: BLE001 — skills registry optional
            logger.debug("installed skills check skipped", exc_info=True)

        # 5. Existing Research: 同题研究已存在
        existing = await self._find_existing_research(gap)
        rep.existing_research_hit = existing is not None

        return rep

    async def _on_mandatory_asset(self, gap: MethodGap) -> bool:
        """Gap 的来源节点是否产自 MANDATORY 资产（provenance 追溯）。"""
        pid = await self._project_of(gap)
        assets = (
            (
                await self._session.execute(
                    select(MethodAsset).where(
                        MethodAsset.project_id == pid,
                        MethodAsset.authority == MethodAssetAuthority.MANDATORY,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not assets:
            return False
        mandatory_ids = {a.id for a in assets}
        # gap.scope_ref 形如 "procedure_node:{id}" → 查节点 provenance
        ref = (gap.scope_ref or "").replace("procedure_node:", "")
        if not ref.isdigit():
            return False
        from project_runtime.project_state.models import ProcedureNode

        node = await self._session.get(ProcedureNode, int(ref))
        if node is None:
            return False
        try:
            prov = json.loads(node.provenance or "{}")
        except ValueError:
            return False
        return prov.get("asset_id") in mandatory_ids

    async def _project_of(self, gap: MethodGap) -> int:
        """Resolve gap → project via its assessment (cached per resolver)."""
        if not hasattr(self, "_proj_cache"):
            self._proj_cache = {}
        if gap.assessment_id not in self._proj_cache:
            from project_runtime.project_state.models import (
                ProjectReadinessAssessment,
            )

            stmt = select(ProjectReadinessAssessment.project_id).where(
                ProjectReadinessAssessment.id == gap.assessment_id
            )
            self._proj_cache[gap.assessment_id] = (
                await self._session.execute(stmt)
            ).scalar_one()
        return self._proj_cache[gap.assessment_id]

    async def _find_existing_research(
        self, gap: MethodGap
    ) -> ResearchRecord | None:
        question = self._question_of(gap)
        kind = ROUTE_TABLE.get(gap.gap_type.value, "")
        stmt = select(ResearchRecord).where(
            ResearchRecord.question == question,
            ResearchRecord.research_kind == kind,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _get_or_create_research(
        self, gap: MethodGap, gtype: str, route: str
    ) -> ResearchRecord:
        rec = await self._find_existing_research(gap)
        if rec is not None:
            return rec
        rec = ResearchRecord(
            project_id=await self._project_of(gap),
            research_kind=route,
            question=self._question_of(gap),
            gap_id=gap.id,
            status="OPEN",
            summary=None,
            findings_json=None,
            source_refs_json=json.dumps([gap.scope_ref or ""], ensure_ascii=False),
        )
        self._session.add(rec)
        await self._session.flush()
        return rec

    def _question_of(self, gap: MethodGap) -> str:
        return (gap.description or gap.scope_ref or f"gap-{gap.id}")[:500]

# 导入 AssetType 供 typing 引用（避免 ruff F401）
_ = AssetType
