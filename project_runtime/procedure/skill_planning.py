"""Skill-informed Planning (偏离① 兑现).

用户设想: skill 指导项目流程创建——方法不足（文档是需求/功能规格而非
过程方法）时，绑定的 PROJECT_METHOD skill 提供流程骨架（stages），
用户文档的功能点归入对应阶段，而非拆成平铺的功能模块。

红线（V1.12 延续）:
  - skill 流程只补 Gap（RECOMMENDED），不覆盖用户 MANDATORY 方法
  - 来源全程可溯（source.type=SKILL_METHOD + skill_id/version）
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import ProcedureNode

logger = logging.getLogger(__name__)

# 需求/功能规格类文档的判定信号（understanding 域假设 + 文档措辞）
_REQUIREMENT_DOC_SIGNALS = (
    "功能", "接口", "API", "需求", "特性", "模块",
    "接口文档", "需求跟踪", "字段", "表结构", "端点",
)
# 过程/方法类文档的判定信号
_METHOD_DOC_SIGNALS = (
    "阶段", "流程", "生命周期", "工作流", "步骤", "里程碑", "交付物",
)


def classify_document_mode(
    domain: str, project_type: str, doc_text: str
) -> str:
    """判定文档是过程方法还是需求规格 → 抽取模式。

    Returns:
      "METHOD"    文档含明确过程结构——按原文抽阶段（现行行为）
      "REQUIREMENT" 文档是需求/功能规格——需要 skill 补流程骨架
    """
    text = doc_text[:8000]
    method_hits = sum(1 for k in _METHOD_DOC_SIGNALS if k in text)
    req_hits = sum(1 for k in _REQUIREMENT_DOC_SIGNALS if k in text)
    # 过程信号占优（且显著）→ METHOD；否则需求规格倾向
    if method_hits >= 3 and method_hits > req_hits:
        return "METHOD"
    return "REQUIREMENT"


async def read_skill_stages(
    session: AsyncSession, project_id: int
) -> list[dict] | None:
    """项目绑定的 ENABLED PROJECT_METHOD skill → stages 骨架。

    Returns: [{"name": 阶段名, "expected_outputs": [...]}] or None
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
            .order_by(SkillBinding.id.desc())
            .limit(1)
        )
        sk = (await session.execute(stmt)).scalar_one_or_none()
        if sk is None:
            return None

        stages: list[dict] = []
        if sk.stages:
            try:
                raw = json.loads(sk.stages)
            except ValueError:
                raw = None
            if isinstance(raw, list):
                for st in raw:
                    stages.append(
                        {"name": st if isinstance(st, str) else str(st)}
                    )
        if sk.expected_outputs:
            try:
                outs = json.loads(sk.expected_outputs)
                if isinstance(outs, list) and stages:
                    # 分配到最贴切的阶段（V0.1：顺序对齐，多余进末阶段）
                    for i, o in enumerate(outs):
                        idx = min(i, len(stages) - 1)
                        stages[idx].setdefault("expected_outputs", [])
                        stages[idx]["expected_outputs"].append(o)
            except ValueError:
                pass
        if not stages:
            return None
        for st in stages:
            st["skill_id"] = sk.skill_id
            st["skill_version"] = sk.version
        return stages
    except Exception:  # noqa: BLE001 — skill 是可选增强，绝不阻断
        logger.debug("skill stages load failed", exc_info=True)
        return None


def map_nodes_to_stages(
    stages: list[dict], nodes: list[ProcedureNode]
) -> list[dict]:
    """用户功能节点归入 skill 阶段（V0.1 关键词亲和映射）。

    规则：每个功能节点按名称/活动关键词找最亲和的阶段；找不到的
    归入"开发实现"类阶段（或最后一个阶段）。输出阶段骨架 workstreams。
    """
    # 阶段亲和关键词（常见交付阶段 → 功能词）
    affinity = {
        "需求": ["需求", "基线", "确认", "澄清"],
        "理解": ["理解", "分析", "调研"],
        "解决方案": ["方案", "选型", "设计思路"],
        "原型": ["原型", "界面", "mock"],
        "架构": ["架构", "结构", "模型", "表", "数据"],
        "开发": ["实现", "开发", "接口", "API", "功能", "编码", "端点"],
        "测试": ["测试", "验证", "用例", "验收"],
        "交付": ["交付", "打包", "部署", "上线"],
        "文档": ["文档", "说明", "手册"],
    }

    def stage_score(stage_name: str, node: ProcedureNode) -> int:
        kws = []
        for key, words in affinity.items():
            if key in stage_name:
                kws = words
                break
        if not kws:
            kws = affinity["开发"]
        text = " ".join(
            filter(None, [node.name, node.activities or "",
                          node.outputs or ""])
        )
        return sum(1 for k in kws if k in text)

    # 归类：全 0 分（无亲和信号）默认归"开发实现"类阶段而非首阶段
    dev_idx = next(
        (i for i, st in enumerate(stages) if "开发" in st["name"]),
        len(stages) - 1,
    )
    stage_nodes: dict[int, list[ProcedureNode]] = {i: [] for i in range(len(stages))}
    for node in nodes:
        scores = [stage_score(st["name"], node) for st in stages]
        best = max(range(len(scores)), key=lambda i: scores[i])
        if scores[best] == 0:
            best = dev_idx
        stage_nodes[best].append(node)

    workstreams: list[dict] = []
    for i, st in enumerate(stages):
        assigned = stage_nodes[i]
        ws = {
            "name": st["name"],
            "workstream": st["name"],
            "activities": (
                "；".join(n.name for n in assigned)
                if assigned
                else "按该阶段目标推进（skill 方法论）"
            ),
            "inputs": (
                "；".join(
                    n.inputs for n in assigned if n.inputs and n.inputs != "UNKNOWN"
                ) or "上一阶段产出"
            ),
            "outputs": (
                "；".join(
                    filter(
                        None,
                        [
                            *(n.outputs for n in assigned
                              if n.outputs and n.outputs != "UNKNOWN"),
                            *(st.get("expected_outputs") or []),
                        ],
                    )
                ) or "阶段交付物"
            ),
            "roles": None,
            "dependencies": stages[i - 1]["name"] if i > 0 else None,
            "gate": None,
            "acceptance_criteria": None,
            "constraints": None,
            "source": {
                "type": "SKILL_METHOD",
                "authority": "RECOMMENDED",  # 只补 Gap，不覆盖用户方法
                "skill_id": st.get("skill_id"),
                "skill_version": st.get("skill_version"),
                "asset_id": None,
                "procedure_node_id": None,
                "mapped_node_ids": [n.id for n in assigned],
            },
        }
        workstreams.append(ws)
    return workstreams
