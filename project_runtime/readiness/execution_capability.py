"""Execution Capability Readiness (WP-038 / WP-027 §Execution Capability).

对照 Procedure 真正要求的能力（per-node role/tool/skill 线索）与运行时
实际可用能力（Worker 注册、Skill 绑定、Capability providers），输出
covered_by_* / missing_* 结构与独立的 execution_readiness。

关键立场（WP-038 Gate 4 + 10-规范规则 5）：
  - Skill=0 本身不触发 Research、不判 MISSING——检查的是「任务真正
    要求的能力是否存在」，不是「系统有没有某组固定 Skill」。
  - Method / Execution Readiness 可分别为 READY / MISSING（独立两轴，
    互不裹挟——方法烂≠没能力，能力全≠方法够）。

Extension Points (CR-METHOD-POINTS 后续三登记):
  method.readiness.evaluators — 评估策略可换
  gap.classifiers            — 分类策略可换
  gap.resolvers              — 解决策略可换（WP-029 消费）
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import ProcedureNode

logger = logging.getLogger(__name__)

UNKNOWN = "UNKNOWN"


class ExecutionCapabilityReadiness:
    """Per-node execution capability evaluation against live runtime."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def assess(
        self, project_id: int, model_id: int, *, executor=None
    ) -> dict:
        """Evaluate execution capability → serializable snapshot dict.

        The snapshot is stored by the caller (assessor composition) into
        ProjectReadinessAssessment.execution_capability_readiness.
        """
        nodes = (
            (
                await self._session.execute(
                    select(ProcedureNode).where(ProcedureNode.model_id == model_id)
                )
            )
            .scalars()
            .all()
        )

        # live capability sources (V0.1: built-in LLM Worker runtime is
        # always available; skills/bindings/providers are discovered via
        # their registries — checked without requiring any fixed skill set)
        available = await self._available_capabilities(project_id)

        node_results: list[dict] = []
        for n in nodes:
            req = self._node_requirements(n)
            covered: dict[str, str] = {}
            missing: dict[str, str] = {}

            for cap in req:
                if cap in available:
                    covered[cap] = f"covered_by_{available[cap]}"
                else:
                    missing[cap] = f"missing_{cap}"

            # Node execution readiness: nothing missing → READY
            node_results.append({
                "node_id": n.id,
                "node_name": n.name,
                "requirements": req,
                "covered": covered,
                "missing": missing,
                "execution_readiness": (
                    "READY" if not missing
                    else ("PARTIAL" if covered else "MISSING")
                ),
            })

        # Extension: evaluators may refine (advisory — Core decides)
        if executor is not None:
            try:
                await executor.fire_hook(
                    "method.readiness.evaluators",
                    {"project_id": project_id, "nodes": node_results},
                )
            except Exception:  # noqa: BLE001 — advisory, never blocks
                logger.debug("readiness evaluators hook failed", exc_info=True)

        ready = sum(1 for r in node_results if r["execution_readiness"] == "READY")
        partial = sum(1 for r in node_results if r["execution_readiness"] == "PARTIAL")
        overall = (
            "READY" if node_results and ready == len(node_results)
            else ("MISSING" if node_results and ready + partial == 0
                  else ("PARTIAL" if node_results else "MISSING"))
        )
        return {
            "overall": overall,
            "total_nodes": len(node_results),
            "ready": ready,
            "partial": partial,
            "nodes": node_results,
            "available_capabilities": sorted(available.values()),
        }

    async def _available_capabilities(self, project_id: int) -> dict[str, str]:
        """What the runtime can actually do right now.

        Built-ins: LLM Worker runtime + artifact manager (always present in
        V0.1). Optional: enabled skill bindings (checked per project) and
        registered capability providers — absence of these is NOT failure,
        it just means those coverage paths report missing.
        """
        caps: dict[str, str] = {
            "worker_runtime": "worker",          # built-in LLM Worker
            "artifact_processor": "worker",      # ArtifactManager built-in
        }
        # bound skills (project-level bindings, ENABLED only)
        try:
            from project_runtime.skills.enums import SkillStatus
            from project_runtime.skills.models import (
                SkillBinding,
                SkillDefinition,
            )

            # SkillBinding.skill_id is the string registry key — join on it
            stmt = (
                select(SkillDefinition)
                .join(
                    SkillBinding,
                    SkillBinding.skill_id == SkillDefinition.skill_id,
                )
                .where(
                    SkillBinding.project_id == project_id,
                    SkillDefinition.status == SkillStatus.ENABLED,
                )
            )
            skills = (
                (await self._session.execute(stmt)).scalars().all()
            )
            for sk in skills:
                caps[f"skill:{sk.skill_id}"] = "skill"
        except Exception:  # noqa: BLE001 — skills registry optional
            logger.debug("skill capability lookup skipped", exc_info=True)
        return caps

    def _node_requirements(self, node: ProcedureNode) -> dict[str, str]:
        """Extract capability requirements a node actually declares.

        V0.1 deterministic extraction: roles (human roles are advisory info,
        NOT runtime requirements), constraints mentioning tools/skills, and
        task-type hints from the node name. Absent declarations require
        only the built-in worker runtime — a node with no special needs is
        executable, never MISSING for unspecified wants.
        """
        # capability keys double as lookup keys into _available_capabilities
        req: dict[str, str] = {"worker_runtime": "worker_runtime"}

        text = " ".join(
            filter(None, [node.name, node.constraints or "",
                          node.activities or ""])
        ).lower()

        # explicit tool/skill mentions in constraints
        for kw, cap in (
            ("浏览器", "browser"), ("browser", "browser"),
            ("检索", "retrieval"), ("search", "retrieval"),
            ("research", "research"), ("调研", "research"),
        ):
            if kw in text:
                req[cap] = cap  # covered only if a provider/skill supplies it
        return req
