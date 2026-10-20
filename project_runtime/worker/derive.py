"""Controller 派生模板白名单推导（返修-20号-V1.4 §2.1——三段式，上限语义）.

硬门: 推导结果恒 ⊆ 人工定义的环节上限——Controller 再聪明也开不出
新权限; 最坏是给少了（降级，任务继续），不会给多了（越权）。

三段:
  ① 环节上限（天花板）: 继承该环节内置模板的 plugins 全集
     —— 上限是人工评审过的静态代码（§2.3 三层依据），非 LLM 推导物
  ② 契约增量（只加安全集）: 扫 Task.contract 能力词，从安全增量集加
     {arch_diagram, slide_generator, todo, builder, test_runner}
     —— 全部只读或工作区禁闭型，无文件系统越界面
     —— shell / git 永不进增量集（只能经 ① 的环节上限获得）
  ③ misc 兜底: 无对应环节 → 三能力基线（file_rw + web_search）
  skills 恒 None（skill 选择由内容匹配负责——用户定则"安装即可用"）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentSource,
)

logger = logging.getLogger(__name__)

# 三能力基线（V1.4 §1.3——每模板必含）
BASELINE_PLUGINS = ("file_rw", "web_search")

# 契约安全增量集（§2.1 ②——只读/工作区禁闭型，shell/git 永不在）
SAFE_INCREMENT_PLUGINS = {
    "arch_diagram", "slide_generator", "todo", "builder", "test_runner",
}

# 契约能力词 → 插件（扫描 Task.contract 的关键词映射）
_CONTRACT_PLUGIN_KEYWORDS: list[tuple[str, str]] = [
    (r"架构图|流程图|时序图|mermaid|diagram", "arch_diagram"),
    (r"PPT|幻灯|汇报材料|演示文稿|slides?", "slide_generator"),
    (r"待办|任务清单|todo", "todo"),
    (r"构建|打包|docker|镜像|npm build", "builder"),
    (r"测试|pytest|单测|用例", "test_runner"),
]

# 数量闸（§2.2——每项目派生模板上限）
CONTROLLER_TEMPLATE_CAP = 11  # 九环节 + 2


def derive_controller_whitelist(
    stage: str, task_contract: str | None,
    builtin_plugins: list[str] | None,
) -> tuple[list[str], list[str] | None]:
    """三段推导（纯函数——可单测）。

    builtin_plugins: 该环节内置模板的 plugins（调用方从库里查——
    纯函数不触库）; None=无对应环节。
    返回 (plugins, skills)。
    """
    plugins: list[str] = []

    # ① 环节上限（天花板 = 内置模板 plugins 全集）
    ceiling = list(builtin_plugins) if builtin_plugins else []

    # ③ misc 兜底: 无对应环节 → 三能力基线（+ 契约安全增量——
    # 增量集本身只读/禁闭型，基线上叠加不越权）
    if not ceiling:
        plugins = list(BASELINE_PLUGINS)
        contract = task_contract or ""
        for pattern, plugin in _CONTRACT_PLUGIN_KEYWORDS:
            if plugin in SAFE_INCREMENT_PLUGINS and plugin not in plugins:
                if re.search(pattern, contract, re.IGNORECASE):
                    plugins.append(plugin)
        return plugins, None

    # ① 继承环节上限
    plugins = list(ceiling)

    # ② 契约增量（只加安全集，且 ⊆ 上限∪安全集——增量本身即授权）
    contract = task_contract or ""
    for pattern, plugin in _CONTRACT_PLUGIN_KEYWORDS:
        if plugin in SAFE_INCREMENT_PLUGINS and plugin not in plugins:
            if re.search(pattern, contract, re.IGNORECASE):
                plugins.append(plugin)

    # 基线保底（内置模板必含——防御旧库未 upsert 的场景）
    for base in BASELINE_PLUGINS:
        if base not in plugins:
            plugins.append(base)

    return plugins, None


def controller_template_signature(
    stage: str, task_contract: str | None,
) -> str:
    """幂等闸签名: stage + 契约能力词指纹（同签名不重复建）。"""
    words = []
    contract = task_contract or ""
    for pattern, plugin in _CONTRACT_PLUGIN_KEYWORDS:
        if re.search(pattern, contract, re.IGNORECASE):
            words.append(plugin)
    raw = f"{stage}|{'+'.join(sorted(set(words)))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def ensure_controller_template(
    session: AsyncSession,
    project_id: int,
    stage: str,
    task_contract: str | None,
) -> AgentDefinition | None:
    """route-miss 时派生 CONTROLLER_GENERATED 模板（§2.2 派生链）。

    双闸:
      - 幂等闸: 同签名（stage+契约能力词指纹）已存在 → 复用不重建
      - 数量闸: 项目内该来源模板数超上限 → 不建（落老路径）
    返回模板（新建或复用）; 触闸返回 None。
    """
    try:
        from project_runtime.worker.registry import (
            BUILT_IN_TEMPLATES,
            normalize_stage,
        )

        norm_stage = normalize_stage(stage)
        sig = controller_template_signature(norm_stage, task_contract)

        # 幂等闸: 同签名模板已存在（本项目 scope 或 GLOBAL）→ 复用
        stmt = select(AgentDefinition).where(
            AgentDefinition.source == AgentSource.CONTROLLER_GENERATED,
            AgentDefinition.agent_id.like(f"controller-{sig}%"),
        )
        existing = (
            (await session.execute(stmt)).scalars().first()
        )
        if existing is not None:
            return existing

        # 数量闸: 本项目派生模板上限（九环节 + 2）
        count_stmt = select(AgentDefinition).where(
            AgentDefinition.source == AgentSource.CONTROLLER_GENERATED,
            AgentDefinition.scope == "PROJECT",
        )
        rows = (await session.execute(count_stmt)).scalars().all()
        project_generated = [
            r for r in rows if r.scope == "PROJECT"
            # scope 列已过滤; agent_id 前缀按 sig 区分项目归属
        ]
        if len(project_generated) >= CONTROLLER_TEMPLATE_CAP:
            logger.warning(
                "controller template cap %d reached for stage %s — "
                "fallback to legacy path",
                CONTROLLER_TEMPLATE_CAP, norm_stage,
            )
            return None

        # ① 查该环节内置模板的 plugins（环节上限）
        ceiling_plugins: list[str] | None = None
        ceiling_name = None
        for tpl in BUILT_IN_TEMPLATES:
            if norm_stage in (tpl["stage_affinity"] or []):
                ceiling_plugins = list(tpl["plugins"])
                ceiling_name = tpl["agent_id"]
                break

        # 三段推导
        plugins, skills = derive_controller_whitelist(
            norm_stage, task_contract, ceiling_plugins
        )

        # 建模板（scope=PROJECT——§2.2 门: 升 GLOBAL 须用户 UI 确认）
        d = AgentDefinition(
            agent_id=f"controller-{sig}-{norm_stage}",
            name=f"派生-{norm_stage}",
            role_description=(
                f"Controller 派生（环节: {norm_stage}）——"
                f"上限继承自 {ceiling_name or '基线'}"
            ),
            plugins=plugins,
            skills=skills,
            stage_affinity=[norm_stage],
            source=AgentSource.CONTROLLER_GENERATED,
            scope="PROJECT",
        )
        session.add(d)
        await session.flush()

        # 审计事件（§2.3——payload 带推导依据）。
        # RH-03 铁律: 持写事务组件内埋事件必须用调用方 session 同事务
        # 直插——禁走独立 EventEngine session（跨库/跨连接在 SQLite 单写者
        # 下互相拖锁，并发 runner 直接 database is locked——实测教训）
        try:
            from project_runtime.event.models import (  # noqa: F401
                RuntimeEvent,
                RuntimeEventType,
            )

            session.add(RuntimeEvent(
                event_type=RuntimeEventType.AGENT_CREATED.value,
                project_id=project_id,
                phase="CONTROLLER_DERIVE",
                message=f"派生模板 {d.agent_id}: {len(plugins)} 插件",
                actor="CONTROLLER",
                payload=json.dumps({
                    "agent_id": d.agent_id,
                    "stage": norm_stage,
                    "plugins": plugins,
                    "ceiling_from": ceiling_name,
                    "contract_increment": sorted(
                        set(plugins) - set(ceiling_plugins or [])
                    ),
                    "signature": sig,
                    "rule": (
                        "①环节上限继承"
                        if ceiling_name else "③misc 基线兜底"
                    ),
                }, ensure_ascii=False),
            ))
            await session.flush()
        except Exception:  # noqa: BLE001 — 审计 best-effort
            logger.debug("derive audit event failed", exc_info=True)

        return d
    except Exception:  # noqa: BLE001 — 派生失败落老路径
        logger.debug("ensure_controller_template failed", exc_info=True)
        return None
