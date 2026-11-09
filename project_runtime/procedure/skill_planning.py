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
    session: AsyncSession, project_id: int, *, doc_text: str = ""
) -> list[dict] | None:
    """PROJECT_METHOD skill → stages 骨架。

    选择顺序（用户需求: 程序根据任务内容自动选 skill）:
      1. 项目显式绑定的 ENABLED skill（用户意志优先）
      2. 无绑定 → LLM 选择器从注册表自动选
         （name + description + when_to_use 作为 tool 列表，
          function calling；LLM 不可用退化关键词匹配）

    Returns: [{"name": 阶段名, "expected_outputs": [...]}] or None
    """
    from project_runtime.skills.enums import SkillStatus, SkillType
    from project_runtime.skills.models import (
        SkillBinding,
        SkillDefinition,
    )

    sk: SkillDefinition | None = None
    try:
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
    except Exception:  # noqa: BLE001 — 绑定查询失败走自动选择
        pass

    if sk is None:
        # 无绑定 → LLM 按项目内容自动选择（偏离①增强: 自动选 skill）
        try:
            from project_runtime.skills.catalog import (
                format_available_skills_xml,
                list_catalog,
            )
            from project_runtime.skills.selector import SkillSelector

            content = doc_text or await _project_doc_text(session, project_id)
            if not content.strip():
                return None
            # F2/收尾：规划侧也能看见全员 Skill 目录（不只 DB 候选）
            catalog_xml = format_available_skills_xml(list_catalog())
            sk = await SkillSelector(session).select(
                task_content=content,
                skill_type=SkillType.PROJECT_METHOD,
                # 返修V3 批A-A5: 中性引导——防多 skill 时代被『软件交付』带偏
                extra_context=(
                    "生成项目流程计划：选择最匹配项目内容的 PROJECT_METHOD skill\n"
                    + catalog_xml
                ),
            )
            if sk is not None and not (sk.stages or "").strip():
                # 选中的 skill 没有 stages 元数据 — 用不了
                sk = None
        except Exception:  # noqa: BLE001 — 自动选择失败不阻断规划
            logger.debug("LLM skill auto-select failed", exc_info=True)
            sk = None

    # FS 权威：DB 无 stages 时，从 installed/ 的 SKILL.md 再试
    if sk is None or not (sk.stages or "").strip():
        fs_stages = _stages_from_fs_catalog()
        if fs_stages:
            return fs_stages

    if sk is None:
        # 返修V3 批B-B1: 禁静默——发 SKILL_GAP（事件+研究单）
        try:
            from project_runtime.skills.discovery import emit_skill_gap

            content = doc_text or await _project_doc_text(session, project_id)
            await emit_skill_gap(
                session,
                project_id,
                domain_summary=content[:800] or f"project:{project_id}",
                skill_type="PROJECT_METHOD",
            )
        except Exception:  # noqa: BLE001 — 信号失败仍不阻断规划
            logger.debug("emit SKILL_GAP failed", exc_info=True)
        return None

    # 解析 stages/expected_outputs（skill 是可选增强，绝不阻断）
    try:
        stages: list[dict] = []
        if sk.stages:
            try:
                raw = json.loads(sk.stages)
            except ValueError:
                raw = None
            if isinstance(raw, list):
                for st in raw:
                    # V1.5a-1 补强B: 双形态——旧 zip 字符串列表照旧,
                    # 新形态 dict（name + outputs）取结构化字段
                    if isinstance(st, dict):
                        entry = {
                            "name": str(st.get("name") or "").strip(),
                        }
                        outs = st.get("outputs")
                        if isinstance(outs, list) and outs:
                            entry["expected_outputs"] = [
                                str(o) for o in outs
                            ]
                        if entry["name"]:
                            stages.append(entry)
                    elif isinstance(st, str) and st.strip():
                        stages.append({"name": st.strip()})
        if sk.expected_outputs and not any(
            st.get('expected_outputs') for st in stages
        ):
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
        # V1.5a-1 序4: code_stage 标记——outputs 含源码类关键词
        # （透传给 Planner/Worker——契约携带纪律/软提醒消费它）
        _code_kws = ("源码", "入口文件", "代码", "工程")
        for st in stages:
            st["skill_id"] = sk.skill_id
            st["skill_version"] = sk.version
            outs = [str(o) for o in (st.get("expected_outputs") or [])]
            st["code_stage"] = any(
                any(k in o for k in _code_kws) for o in outs
            )
        return stages
    except Exception:  # noqa: BLE001
        logger.debug("skill stages load failed", exc_info=True)
        return None


def _stages_from_fs_catalog() -> list[dict] | None:
    """从 data/skills/installed 扫描 frontmatter.stages（agentskills 对齐）。"""
    try:
        from project_runtime.skills.catalog import list_catalog
    except Exception:  # noqa: BLE001
        return None
    import yaml

    for e in list_catalog(include_user_agents=True):
        try:
            text = e.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        try:
            meta = yaml.safe_load(text[3:end])
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        raw = meta.get("stages")
        if not isinstance(raw, list) or not raw:
            continue
        stages: list[dict] = []
        for st in raw:
            if isinstance(st, dict):
                name = str(st.get("name") or "").strip()
                if not name:
                    continue
                entry: dict = {
                    "name": name,
                    "skill_id": str(meta.get("skill_id") or e.name),
                    "skill_version": str(meta.get("version") or "fs"),
                    "code_stage": False,
                }
                outs = st.get("outputs") or st.get("expected_outputs")
                if isinstance(outs, list) and outs:
                    entry["expected_outputs"] = [str(o) for o in outs]
                stages.append(entry)
            elif isinstance(st, str) and st.strip():
                stages.append({
                    "name": st.strip(),
                    "skill_id": str(meta.get("skill_id") or e.name),
                    "skill_version": str(meta.get("version") or "fs"),
                    "code_stage": False,
                })
        if stages:
            logger.info(
                "FS skill stages from %s (%d stages)", e.name, len(stages),
            )
            return stages
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
            # V1.4b 修1: 显式阶段词（Planner task_type 优先级链第 2 源）
            "source_skill_stage": st["name"],
            # V1.5a-1 序4: code_stage 透传 + 阶段产出物（Planner 契约用）
            "code_stage": bool(st.get("code_stage")),
            "stage_outputs": list(st.get("expected_outputs") or []),
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


async def _project_doc_text(session: AsyncSession, project_id: int) -> str:
    """项目方法资料全文（LLM 选 skill 的任务内容输入）。"""
    from project_runtime.project_state.models import MethodAsset

    stmt = (
        select(MethodAsset.content)
        .where(MethodAsset.project_id == project_id)
        .order_by(MethodAsset.id.desc())
        .limit(3)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return "\n\n".join(r or "" for r in rows)
