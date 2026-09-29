"""Project Understanding analyzer (WP-026).

Deterministic V0.1 (keyword/structure-driven). LLM 深化属 WP-037 — the
analyzer interface (`UnderstandingAnalyzer` protocol) is what WP-037 will
implement; the persistence layer and separation rules are final.

Three-way separation rules (acceptance §1):
  - a statement grounded in the user's material          → KnownFact
  - a statement the runtime infers but cannot verify     → Assumption
  - something the material flags as unknown/pending      → OpenQuestion

Method asset recognition (acceptance §2) — five signals:
  流程 / SOP / 实施方案 / 验收材料 / 历史模板

红线 (acceptance §3): hints reference asset ids only. No SkillDefinition
write path exists in this module (enforced by structural test).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.understanding.models import ProjectUnderstanding

logger = logging.getLogger(__name__)

ANALYZER_VERSION = "v0.1"


@dataclass
class MethodAssetHint:
    asset_id: int
    signal: str            # PROCESS / SOP / PLAN / ACCEPTANCE / TEMPLATE
    provenance: str        # always "user-material" in V0.1
    confidence: float
    rationale: str


@dataclass
class UnderstandingResult:
    domain_hypothesis: str = "UNKNOWN"
    project_type_hypothesis: str = "UNKNOWN"
    goal: str = ""
    known_facts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    known_requirements: list[str] = field(default_factory=list)
    knowledge_gaps: list[str] = field(default_factory=list)
    method_asset_hints: list[MethodAssetHint] = field(default_factory=list)


class UnderstandingAnalyzer(Protocol):
    """WP-037 will provide an LLM implementation of this protocol."""

    async def analyze(
        self, goal: str, assets: list[tuple[int, str, str]]
    ) -> UnderstandingResult:
        """goal + [(asset_id, asset_type, content)] → understanding."""
        ...


# ── Deterministic analyzer (V0.1 default) ────────────────────────────────

# Signals that a material is a METHOD asset (流程/SOP/实施方案/验收/模板).
_METHOD_SIGNALS: dict[str, list[str]] = {
    "PROCESS": ["流程", "阶段", "步骤", "工作流", "workflow", "process",
                "生命周期", "工序"],
    "SOP": ["sop", "标准作业", "操作规程", "作业指导", "规范流程"],
    "PLAN": ["实施方案", "实施计划", "交付计划", "方案设计", "implementation plan"],
    "ACCEPTANCE": ["验收", "acceptance", "验收标准", "验收材料", "质检"],
    "TEMPLATE": ["模板", "template", "历史项目", "范例", "样例", "参考项目"],
}

# Headings/keywords that make a statement a requirement (vs. mere fact).
_REQUIREMENT_MARKERS = ["必须", "要求", "需要", "shall", "must", "不得",
                        "禁止", "应支持", "要求支持"]

# Unknown/pending markers → OpenQuestion, never silently assumed.
_OPEN_QUESTION_MARKERS = ["待定", "tbd", "未定", "暂不明确", "待确认",
                          "待讨论", "待补充", "open question", "尚不明确"]

# Goal keywords (first section wins in V0.1).
_GOAL_MARKERS = ["目标", "背景", "goal", "objective", "purpose", "愿景"]

_DOMAIN_HINTS = {
    "web": ["web", "网页", "前端", "后端", "api", "网站"],
    "data": ["数据", "数据库", "报表", "etl", "数仓"],
    "mobile": ["app", "移动端", "安卓", "ios", "小程序"],
    "ai": ["大模型", "llm", "ai", "机器学习", "智能"],
    "infra": ["部署", "运维", "集群", "基础设施", "网络"],
}


class DeterministicAnalyzer:
    """Keyword/structure-driven understanding — no LLM, fully testable."""

    async def analyze(
        self, goal: str, assets: list[tuple[int, str, str]]
    ) -> UnderstandingResult:
        res = UnderstandingResult()
        res.goal = _extract_goal(goal, assets)

        for asset_id, asset_type, content in assets:
            text_l = content.lower()

            # ── method asset hints (acceptance §2) ──────────────────
            for signal, keywords in _METHOD_SIGNALS.items():
                hits = sum(1 for k in keywords if k in text_l)
                if hits >= 2 or (hits == 1 and len(content) > 400):
                    res.method_asset_hints.append(
                        MethodAssetHint(
                            asset_id=asset_id,
                            signal=signal,
                            provenance="user-material",
                            confidence=min(0.9, 0.5 + 0.1 * hits),
                            rationale=f"材料命中 {signal} 信号 x{hits}",
                        )
                    )
                    break  # strongest signal per asset

            # ── three-way separation per section ─────────────────────
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                low = stripped.lower()
                if any(m in low for m in _OPEN_QUESTION_MARKERS):
                    res.open_questions.append(stripped)
                elif any(m in low for m in _REQUIREMENT_MARKERS):
                    res.known_requirements.append(stripped)
                    res.known_facts.append(stripped)  # grounded in material
                elif stripped.startswith("- ") or stripped.startswith("* "):
                    # list items without requirement/open markers → facts
                    res.known_facts.append(stripped.lstrip("-* ").strip())

        # ── hypotheses from goal text ────────────────────────────────
        for domain, kws in _DOMAIN_HINTS.items():
            if any(k in res.goal.lower() for k in kws):
                res.domain_hypothesis = domain.upper()
                break
        if any(k in res.goal.lower() for k in ["开发", "实现", "建设", "构建"]):
            res.project_type_hypothesis = "BUILD_SOFTWARE"
        elif any(k in res.goal.lower() for k in ["调研", "分析", "研究"]):
            res.project_type_hypothesis = "RESEARCH"

        # ── assumptions (runtime inference, NOT grounded) ────────────
        if res.domain_hypothesis == "UNKNOWN" and res.goal:
            res.assumptions.append(
                "领域未从材料中识别——按通用项目处理（推断，非事实）"
            )
        if not res.method_asset_hints and assets:
            res.assumptions.append(
                "材料中未识别到方法资产信号——方法充分性待 WP-037 评估（推断，非事实）"
            )
        if not assets:
            res.knowledge_gaps.append("无任何项目材料——Research/澄清需要补足")
        if not res.goal:
            res.open_questions.append("项目目标缺失——需要用户澄清")

        return res


def _extract_goal(goal: str, assets: list[tuple[int, str, str]]) -> str:
    if goal.strip():
        return goal.strip()
    # Fall back to the first goal-like section of the first asset.
    for _, _, content in assets:
        m = re.search(
            r"^#{1,3}\s*(?:[0-9.]*\s*)?(目标|背景|goal|objective)\s*$",
            content, re.MULTILINE | re.IGNORECASE,
        )
        if m:
            section = content[m.end():]
            nxt = re.search(r"^#{1,3}\s", section, re.MULTILINE)
            body = section[: nxt.start()] if nxt else section
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            if lines:
                return " / ".join(lines[:3])
    return ""


# ── Service: persist an understanding snapshot for a project ─────────────


class UnderstandingService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def analyze_project(
        self,
        project_id: int,
        *,
        goal_override: str | None = None,
        analyzer: UnderstandingAnalyzer | None = None,
    ) -> ProjectUnderstanding:
        """Analyze a project's goal + method assets → persisted snapshot."""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        stmt = select(MethodAsset).where(MethodAsset.project_id == project_id)
        assets = list((await self._session.execute(stmt)).scalars().all())
        # snapshot before any commit
        asset_tuples = [(a.id, a.asset_type.value, a.content) for a in assets]

        goal = goal_override or (project.goal or "" if hasattr(project, "goal") else "")

        analyzer = analyzer or DeterministicAnalyzer()
        result = await analyzer.analyze(goal, asset_tuples)

        row = ProjectUnderstanding(
            project_id=project_id,
            domain_hypothesis=result.domain_hypothesis,
            project_type_hypothesis=result.project_type_hypothesis,
            goal=result.goal,
            known_facts_json=_j(result.known_facts),
            assumptions_json=_j(result.assumptions),
            open_questions_json=_j(result.open_questions),
            known_requirements_json=_j(result.known_requirements),
            knowledge_gaps_json=_j(result.knowledge_gaps),
            method_asset_hints_json=_j(
                [
                    {
                        "asset_id": h.asset_id,
                        "signal": h.signal,
                        "provenance": h.provenance,
                        "confidence": h.confidence,
                        "rationale": h.rationale,
                    }
                    for h in result.method_asset_hints
                ]
            ),
            analyzer_version=ANALYZER_VERSION,
        )
        self._session.add(row)
        await self._session.flush()
        return row


def _j(v: list) -> str | None:
    return json.dumps(v, ensure_ascii=False) if v else None
