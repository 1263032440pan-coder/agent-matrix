"""Method Asset Discovery (WP-037 / 10-规范 主流程第二站).

从用户材料中发现方法资产并分类 enrichment，交给 ProcedureNormalizer
转化为 Project Procedure Model。

与 WP-026 的关系: Understanding 的 method_asset_hints（五信号）是本站的
先验输入——hint 的信号映射到八类 AssetType 的初值，Discovery 复核并
补充 authority / meta。Understanding 不负责假设"用户没有方法"（WP-026
红线），Discovery 负责把发现的方法结构化。

Extension Points (CR-METHOD-POINTS):
  method.assets.providers    资产发现来源扩展（MIDDLEWARE kind，ENRICH）
  method.assets.processors   资产加工（过滤/增强/去重）
  procedure.normalizers      规范化策略可换（换 extractor 而不改 Core）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import AssetType
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.understanding.models import ProjectUnderstanding

logger = logging.getLogger(__name__)

# WP-026 五信号 → 八类 AssetType 映射（Understanding hint 初值）
_SIGNAL_TO_TYPE: dict[str, AssetType] = {
    "PROCESS": AssetType.PROCESS,
    "SOP": AssetType.SOP,
    "PLAN": AssetType.PROCESS,          # 实施方案 → 流程类
    "ACCEPTANCE": AssetType.ACCEPTANCE_RULE,
    "TEMPLATE": AssetType.TEMPLATE,
}

# 内容信号的类型复核词表（不依赖 hint 也可分类）
_CONTENT_SIGNALS: dict[AssetType, list[str]] = {
    AssetType.SOP: ["sop", "标准作业", "操作规程", "作业指导"],
    AssetType.STANDARD: ["标准", "规范", "standard", "国标", "行标"],
    AssetType.TEMPLATE: ["模板", "template", "范例", "样例"],
    AssetType.HISTORICAL_REFERENCE: ["历史项目", "上次项目", "参考项目", "以往"],
    AssetType.ACCEPTANCE_RULE: ["验收", "acceptance", "质检", "放行标准"],
    AssetType.ROLE_DEFINITION: ["角色", "职责", "role", "responsibility", "岗位"],
    AssetType.DELIVERY_RULE: ["交付", "delivery", "发布流程", "上线流程"],
    AssetType.PROCESS: ["流程", "阶段", "步骤", "workflow", "process"],
}

UNKNOWN = "UNKNOWN"


@dataclass
class DiscoveredAsset:
    """Discovery 结果条目——复核后的类型 + enrichment + 追溯。"""

    asset_id: int
    asset_type: AssetType
    matched_signal: str
    from_hint: bool          # 分类来自 WP-026 hint 还是内容复核
    confidence: float
    rationale: str
    node_count: int = 0      # 经 normalizer 后回填（discovery 不产节点）
    errors: list[str] = field(default_factory=list)


class MethodAssetDiscovery:
    """多文档方法资产发现 + 分类 + enrichment。

    V0.1 确定性实现。extension 点已接 executor（providers/processors），
    本类是 Core 编排——extension 可换发现/加工策略，不改本类。
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def discover(
        self, project_id: int, *, executor=None
    ) -> list[DiscoveredAsset]:
        """发现项目全部方法资产 → 复核分类（消费 WP-026 hints 先验）。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        # 1. WP-026 hints 作为先验（最新一次理解快照）
        hints: dict[int, dict] = {}
        stmt = (
            select(ProjectUnderstanding)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        snap = (await self._session.execute(stmt)).scalar_one_or_none()
        if snap is not None and snap.method_asset_hints_json:
            for h in json.loads(snap.method_asset_hints_json):
                hints[h["asset_id"]] = h

        # 2. extension: providers 可补充发现来源（V0.1 无内置 provider，
        #    点位已登记——外部插件经 bridge 注册即可参与）
        if executor is not None:
            try:
                await executor.fire_hook(
                    "method.assets.providers",
                    {"project_id": project_id, "known_asset_ids": list(hints)},
                )
            except Exception:  # noqa: BLE001 — advisory, never blocks
                logger.debug("providers hook failed", exc_info=True)

        # 3. 逐资产复核分类（hint 先验 + 内容信号）
        assets = list(
            (
                await self._session.execute(
                    select(MethodAsset).where(MethodAsset.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        results: list[DiscoveredAsset] = []
        for a in assets:
            results.append(self._classify(a, hints.get(a.id)))

        # 4. extension: processors 加工结果（过滤/增强——advisory）
        if executor is not None:
            try:
                payload = {
                    "project_id": project_id,
                    "assets": [
                        {
                            "asset_id": r.asset_id,
                            "asset_type": r.asset_type.value,
                            "confidence": r.confidence,
                        }
                        for r in results
                    ],
                }
                await executor.fire_hook("method.assets.processors", payload)
            except Exception:  # noqa: BLE001 — advisory, never blocks
                logger.debug("processors hook failed", exc_info=True)

        # 5. enrichment 落库（meta_json 记录 discovery 依据，不改 authority——
        #    authority 是用户显式声明，程序不覆盖）
        for r in results:
            asset = next(a for a in assets if a.id == r.asset_id)
            meta = json.loads(asset.meta_json or "{}")
            meta["discovery"] = {
                "matched_signal": r.matched_signal,
                "from_hint": r.from_hint,
                "confidence": r.confidence,
                "rationale": r.rationale,
            }
            asset.meta_json = json.dumps(meta, ensure_ascii=False)
        await self._session.flush()
        return results

    def _classify(
        self, asset: MethodAsset, hint: dict | None
    ) -> DiscoveredAsset:
        # hint 先验：WP-026 已识别的信号直接映射类型
        if hint is not None:
            t = _SIGNAL_TO_TYPE.get(hint.get("signal", ""), AssetType.PROCESS)
            return DiscoveredAsset(
                asset_id=asset.id,
                asset_type=t,
                matched_signal=hint.get("signal", UNKNOWN),
                from_hint=True,
                confidence=hint.get("confidence", 0.5),
                rationale=f"WP-026 hint: {hint.get('rationale', '')}",
            )

        # 内容复核：按信号强度取最高命中的类型
        text_l = asset.content.lower()
        best_type, best_hits = AssetType.PROCESS, 0
        for t, kws in _CONTENT_SIGNALS.items():
            hits = sum(1 for k in kws if k in text_l)
            if hits > best_hits:
                best_type, best_hits = t, hits
        if best_hits == 0:
            return DiscoveredAsset(
                asset_id=asset.id,
                asset_type=asset.asset_type,  # 保留上传时声明类型
                matched_signal=UNKNOWN,
                from_hint=False,
                confidence=0.3,
                rationale="无内容信号——保留上传声明类型",
            )
        return DiscoveredAsset(
            asset_id=asset.id,
            asset_type=best_type,
            matched_signal=best_type.value,
            from_hint=False,
            confidence=min(0.85, 0.5 + 0.1 * best_hits),
            rationale=f"内容命中 {best_type.value} 信号 x{best_hits}",
        )
