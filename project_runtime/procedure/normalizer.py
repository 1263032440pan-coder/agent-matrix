"""ProcedureNormalizer — extract structured ProcedureNodes from raw MethodAssets.

Demo V0.1: keyword-driven extraction (KeywordExtractor).
Formal: LLMExtractor via LiteLLM (reserved interface).
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import ReadinessStatus
from project_runtime.project_state.models import (
    MethodAsset,
    ProcedureNode,
    ProjectProcedureModel,
)

UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"


class NormalizerExtractor(Protocol):
    """Pluggable extractor: raw text → list of ProcedureNode dicts."""

    def extract(self, text: str, source_name: str) -> list[dict]:
        ...


# ── KeywordExtractor (V0.1 default — no LLM dependency) ────────────────────


class KeywordExtractor:
    """Heuristic extraction based on markdown sections and keyword patterns."""

    _SECTION_PATTERN = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    _FIELD_KEYWORDS: dict[tuple[str, ...], str] = {
        ("输入", "input", "前置"): "inputs",
        ("依赖", "dependency", "prerequisite"): "dependencies",
        ("活动", "activity", "步骤", "step", "action", "操作"): "activities",
        ("输出", "output", "产出", "产物", "result", "deliverable"): "outputs",
        ("角色", "role", "负责人", "owner", "who"): "roles",
        ("验收", "acceptance", "检查", "check", "review", "gate"): "gate",
        ("标准", "criteria", "条件", "condition"): "acceptance_criteria",
        ("证据", "evidence", "证明", "proof"): "evidence_requirements",
        ("约束", "constraint", "限制", "limit", "restriction"): "constraints",
        ("返工", "rework", "重做", "回退"): "rework_path",
    }

    _AMBIGUOUS_MARKERS = ("待定", "TBD", "未定", "暂不明确", "?")

    def extract(self, text: str, source_name: str) -> list[dict]:
        # Split text by markdown headings
        heading_positions: list[tuple[int, int, str]] = []
        for m in self._SECTION_PATTERN.finditer(text):
            heading_positions.append((m.start(), m.end(), m.group(2).strip()))

        if not heading_positions:
            # No markdown headings — treat entire text as one node
            return [self._parse_node(source_name, text, source_name)]

        nodes: list[dict] = []
        for i, (start, end, heading) in enumerate(heading_positions):
            body_start = end
            body_end = heading_positions[i + 1][0] if i + 1 < len(heading_positions) else len(text)
            body = text[body_start:body_end].strip()
            nodes.append(self._parse_node(heading, body, source_name))
        return nodes

    def _parse_node(self, heading: str, body: str, source_name: str) -> dict:
        node: dict = {
            "name": heading,
            "workstream": None,
            "inputs": UNKNOWN,
            "activities": UNKNOWN,
            "outputs": UNKNOWN,
            "roles": UNKNOWN,
            "dependencies": UNKNOWN,
            "gate": UNKNOWN,
            "acceptance_criteria": UNKNOWN,
            "evidence_requirements": UNKNOWN,
            "constraints": UNKNOWN,
            "rework_path": UNKNOWN,
            "readiness_status": ReadinessStatus.MISSING.value,
            "source_ref": source_name,
        }

        lines = body.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            for keywords, field in self._FIELD_KEYWORDS.items():
                if any(kw in line.lower() for kw in keywords):
                    value = line.split(":", 1)[-1].strip() if ":" in line else line
                    if node[field] == UNKNOWN:
                        node[field] = value
                    else:
                        node[field] += "\n" + value
                    break

        # Heuristic readiness assessment
        filled = sum(
            1
            for k, v in node.items()
            if k not in ("name", "workstream", "readiness_status", "source_ref")
            and isinstance(v, str)
            and v != UNKNOWN
        )
        total = len(
            [k for k in node if k not in ("name", "workstream", "readiness_status", "source_ref")]
        )
        # AMBIGUOUS check — any field contains ambiguous markers
        has_ambiguous = any(
            any(m in (v if isinstance(v, str) else "") for m in self._AMBIGUOUS_MARKERS)
            for v in node.values()
            if isinstance(v, str)
        )

        if filled == 0:
            node["readiness_status"] = ReadinessStatus.MISSING.value
        elif has_ambiguous:
            node["readiness_status"] = ReadinessStatus.AMBIGUOUS.value
        elif filled == total:
            node["readiness_status"] = ReadinessStatus.READY.value
        elif filled < total // 2:
            node["readiness_status"] = ReadinessStatus.PARTIAL.value
        else:
            node["readiness_status"] = ReadinessStatus.PARTIAL.value

        return node


# ── ProcedureNormalizer ─────────────────────────────────────────────────────


class ProcedureNormalizer:
    """Orchestrates: MethodAssets → ProcedureNodes → ProjectProcedureModel."""

    def __init__(self, session: AsyncSession, extractor: NormalizerExtractor | None = None):
        self._session = session
        self._extractor = extractor or KeywordExtractor()

    async def normalize(self, project_id: int) -> ProjectProcedureModel:
        """Process all MethodAssets for a project into a Procedure Model."""
        assets = await self._get_assets(project_id)

        model = ProjectProcedureModel(project_id=project_id, source_map=json.dumps([]))
        self._session.add(model)
        await self._session.flush()

        source_entries: list[dict] = []

        for asset in assets:
            source_name = asset.source_path or f"asset-{asset.id}"
            node_dicts = self._extractor.extract(asset.content, source_name)

            for nd in node_dicts:
                node = ProcedureNode(
                    model_id=model.id,
                    name=nd["name"],
                    workstream=nd.get("workstream"),
                    inputs=nd.get("inputs", UNKNOWN),
                    activities=nd.get("activities", UNKNOWN),
                    outputs=nd.get("outputs", UNKNOWN),
                    roles=nd.get("roles", UNKNOWN),
                    dependencies=nd.get("dependencies", UNKNOWN),
                    gate=nd.get("gate", UNKNOWN),
                    acceptance_criteria=nd.get("acceptance_criteria", UNKNOWN),
                    evidence_requirements=nd.get("evidence_requirements", UNKNOWN),
                    constraints=nd.get("constraints", UNKNOWN),
                    rework_path=nd.get("rework_path", UNKNOWN),
                    readiness_status=ReadinessStatus(nd.get("readiness_status", "MISSING")),
                    source_ref=nd.get("source_ref"),
                    provenance=json.dumps(
                        {"asset_id": asset.id, "asset_type": asset.asset_type.value}
                    ),
                )
                self._session.add(node)
                source_entries.append(
                    {"asset_id": asset.id, "nodes": len(node_dicts)}
                )

        model.source_map = json.dumps(source_entries)
        await self._session.flush()
        return model

    async def get_nodes(self, model_id: int) -> list[ProcedureNode]:
        stmt = select(ProcedureNode).where(ProcedureNode.model_id == model_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_assets(self, project_id: int) -> list[MethodAsset]:
        stmt = select(MethodAsset).where(MethodAsset.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
