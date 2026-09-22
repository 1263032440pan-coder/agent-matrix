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
    """Takes a ProjectReadinessAssessment and produces structured MethodGap records.

    Dimension-mapping classification by default. LLM semantic classification is
    opt-in via GapClassifier.with_llm(session) — never silently read global
    LLM config (tests must not hit real LLM).
    """

    def __init__(self, session: AsyncSession, llm_enabled: bool = False):
        self._session = session
        self._llm_enabled = llm_enabled

    @classmethod
    def with_llm(cls, session: AsyncSession) -> "GapClassifier":
        return cls(session, llm_enabled=True)

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
        # Batch 3: batch LLM classification — one call for all gaps (not per-gap)
        llm_types = await self._classify_gaps_batch_llm(raw_gaps)
        for i, raw in enumerate(raw_gaps):
            gap_type = llm_types[i] if llm_types else self._classify_gap_type_fallback(raw)
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

    def _classify_gap_type_fallback(self, raw_gap: dict) -> GapType:
        """Dimension-mapping fallback (only EXPLICITNESS/PROCESS reachable)."""
        dim = raw_gap.get("dimension", "")
        return _DIMENSION_TO_GAP_TYPE.get(dim, GapType.EXPLICITNESS)

    async def _classify_gaps_batch_llm(
        self, raw_gaps: list[dict]
    ) -> list[GapType] | None:
        """One LLM call classifies all gaps. Returns None when LLM off/fails."""
        if not self._llm_enabled or not raw_gaps:
            return None
        try:
            from project_runtime.llm import load_llm_settings

            cfg = load_llm_settings()
            if not (cfg.base_url and cfg.api_key and cfg.model):
                return None
        except Exception:
            return None

        import litellm

        lines = "\n".join(
            f"{i}. {g.get('reason', '')[:80]}" for i, g in enumerate(raw_gaps)
        )
        prompt = (
            "将下列项目缺口逐条归类为六类之一，仅输出编号+类别（每行一条）：\n"
            "PRIVATE / EXPLICITNESS / DOMAIN / TECHNICAL / PROCESS / CAPABILITY\n\n"
            f"{lines}\n\n输出格式：0: PROCESS\\n1: EXPLICITNESS\\n..."
        )
        model = cfg.model
        known = ("openai/", "azure/", "anthropic/", "huggingface/", "ollama/",
                 "gemini/", "deepseek/", "mistral/")
        if "/" in model and not model.startswith(known):
            model = f"openai/{model}"
        try:
            resp = await litellm.acompletion(
                model=model, api_base=cfg.base_url, api_key=cfg.api_key,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=min(4096, 50 + len(raw_gaps) * 10), timeout=120,
            )
            answer = (resp.choices[0].message.content or "").strip()
            return self._parse_batch(answer, len(raw_gaps))
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("LLM batch gap classify failed: %s", e)
            return None

    @staticmethod
    def _parse_batch(answer: str, count: int) -> list[GapType] | None:
        import re

        result: list[GapType | None] = [None] * count
        for line in answer.splitlines():
            m = re.match(r"\s*(\d+)\s*[:：.]\s*(\w+)", line)
            if not m:
                continue
            idx, word = int(m.group(1)), m.group(2).upper()
            if 0 <= idx < count:
                for gt in GapType:
                    if gt.value in word:
                        result[idx] = gt
                        break
        if any(r is None for r in result):
            return None  # incomplete parse → fallback
        return result  # type: ignore[return-value]

    async def _classify_gap_type(self, raw_gap: dict) -> GapType:
        """Batch 3 (D08-TD01): LLM semantic classification when configured,
        else dimension-based fallback (only EXPLICITNESS/PROCESS reachable)."""
        llm_type = await self._classify_gap_type_llm(raw_gap)
        if llm_type is not None:
            return llm_type
        dim = raw_gap.get("dimension", "")
        return _DIMENSION_TO_GAP_TYPE.get(dim, GapType.EXPLICITNESS)

    async def _classify_gap_type_llm(self, raw_gap: dict) -> GapType | None:
        """LLM classifies gap into one of six GapTypes by content semantics.
        Returns None when LLM disabled/unavailable/fails/returns unknown type."""
        if not self._llm_enabled:
            return None
        try:
            from project_runtime.llm import load_llm_settings

            cfg = load_llm_settings()
            if not (cfg.base_url and cfg.api_key and cfg.model):
                return None
        except Exception:
            return None

        import litellm

        reason = raw_gap.get("reason", "")
        if not reason:
            return None

        prompt = (
            "将下面的项目缺口归类为六类之一，仅输出类别名：\n"
            "PRIVATE（缺用户私有信息/凭据）/ EXPLICITNESS（表述不明）/ "
            "DOMAIN（缺领域知识）/ TECHNICAL（缺技术方案）/ "
            "PROCESS（缺流程环节）/ CAPABILITY（缺执行能力）\n\n"
            f"缺口：{reason}\n维度：{raw_gap.get('dimension', '')}\n"
            f"节点：{raw_gap.get('node_id', '')}\n"
            "类别："
        )
        model = cfg.model
        known = ("openai/", "azure/", "anthropic/", "huggingface/", "ollama/",
                 "gemini/", "deepseek/", "mistral/")
        if "/" in model and not model.startswith(known):
            model = f"openai/{model}"
        try:
            resp = await litellm.acompletion(
                model=model, api_base=cfg.base_url, api_key=cfg.api_key,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20, timeout=30,
            )
            answer = (resp.choices[0].message.content or "").strip().upper()
            for gt in GapType:
                if gt.value in answer:
                    return gt
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("LLM gap classify failed: %s", e)
        return None

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
