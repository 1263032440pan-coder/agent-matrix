"""Agent 生命周期 FSM + 注册表 + 置备器（20号规范 §3/§6）.

六态 FSM: CREATED→IDLE⇄BUSY→PAUSED/DEGRADED→TERMINATED
转移纪律（actor 防护——照搬 Task FSM 模式）:
  PAUSED   仅 Controller/用户（Agent 自己不能）
  →TERMINATED 仅 Controller/用户/生命周期钩子
  BUSY→IDLE 仅执行链回调
非法转移拒绝 + 审计事件。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.event.models import RuntimeEvent  # noqa: F401
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentSource,
    AgentStatus,
)

logger = logging.getLogger(__name__)


class IllegalAgentTransitionError(Exception):
    pass


# ── 六态转移表（§3.1）───────────────────────────────────────────────
# (from, to) -> 允许的 actor 集合；None actor 集合 = 禁止
_AGENT_TRANSITIONS: dict[tuple[AgentStatus, AgentStatus], set[str] | None] = {
    (AgentStatus.CREATED, AgentStatus.IDLE): {"SYSTEM", "CONTROLLER", "USER"},
    (AgentStatus.CREATED, AgentStatus.TERMINATED): {"CONTROLLER", "USER"},
    (AgentStatus.IDLE, AgentStatus.BUSY): {"SYSTEM"},          # 执行链派发
    (AgentStatus.IDLE, AgentStatus.PAUSED): {"CONTROLLER", "USER"},
    (AgentStatus.IDLE, AgentStatus.TERMINATED): {"CONTROLLER", "USER"},
    (AgentStatus.BUSY, AgentStatus.IDLE): {"SYSTEM"},          # 执行链回调
    (AgentStatus.BUSY, AgentStatus.DEGRADED): {"SYSTEM"},      # 失败计数
    (AgentStatus.BUSY, AgentStatus.PAUSED): {"CONTROLLER", "USER"},
    (AgentStatus.PAUSED, AgentStatus.IDLE): {"CONTROLLER", "USER"},
    (AgentStatus.PAUSED, AgentStatus.TERMINATED): {"CONTROLLER", "USER"},
    (AgentStatus.DEGRADED, AgentStatus.IDLE): {"SYSTEM"},      # 冷却自愈
    (AgentStatus.DEGRADED, AgentStatus.TERMINATED): {"CONTROLLER", "USER"},
}

# 允许的 actor
_ACTORS = {"SYSTEM", "CONTROLLER", "USER", "GATE"}

# DEGRADED 参数（§3.2）
_DEGRADED_FAIL_THRESHOLD = 3
_DEGRADED_COOLDOWN_S = 60


class AgentFSM:
    """Agent 实例状态转移服务（actor 防护 + 审计事件 best-effort）。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def transition(
        self,
        instance: AgentInstance,
        to: AgentStatus,
        *,
        actor: str = "SYSTEM",
        reason: str = "",
    ) -> AgentInstance:
        if actor not in _ACTORS:
            raise IllegalAgentTransitionError(f"unknown actor: {actor}")
        key = (instance.status, to)
        allowed = _AGENT_TRANSITIONS.get(key)
        if allowed is None:
            raise IllegalAgentTransitionError(
                f"illegal transition {instance.status.value} → {to.value} "
                f"(no such edge in frozen table)"
            )
        if actor not in allowed:
            raise IllegalAgentTransitionError(
                f"actor {actor} not permitted for "
                f"{instance.status.value} → {to.value} "
                f"(allowed: {sorted(allowed)})"
            )
        prev = instance.status
        instance.status = to
        if to is AgentStatus.DEGRADED:
            instance.degraded_until = datetime.now(timezone.utc) + timedelta(
                seconds=_DEGRADED_COOLDOWN_S
            )
        await self._emit(instance, prev, to, actor, reason)
        await self._session.flush()
        return instance

    async def record_failure(self, instance: AgentInstance) -> AgentInstance:
        """执行失败计数——达阈值转 DEGRADED（§3.2 自愈）。"""
        instance.fail_count += 1
        if instance.fail_count >= _DEGRADED_FAIL_THRESHOLD:
            prev = instance.status
            instance.status = AgentStatus.DEGRADED
            instance.degraded_until = datetime.now(timezone.utc) + timedelta(
                seconds=_DEGRADED_COOLDOWN_S
            )
            instance.fail_count = 0
            await self._emit(instance, prev, AgentStatus.DEGRADED, "SYSTEM",
                             "3 consecutive failures")
        await self._session.flush()
        return instance

    async def try_recover(self, instance: AgentInstance) -> bool:
        """冷却期满的 DEGRADED → IDLE（自愈）。"""
        if instance.status is not AgentStatus.DEGRADED:
            return False
        until = instance.degraded_until
        if until is not None and datetime.now(timezone.utc) < until:
            return False
        prev = instance.status
        instance.status = AgentStatus.IDLE
        instance.degraded_until = None
        await self._emit(instance, prev, AgentStatus.IDLE, "SYSTEM", "cooldown elapsed")
        await self._session.flush()
        return True

    async def _emit(
        self,
        instance: AgentInstance,
        prev: AgentStatus,
        to: AgentStatus,
        actor: str,
        reason: str,
    ) -> None:
        """生命周期事件（§3.3 七类——best-effort，绝不打断主链）。"""
        try:
            from project_runtime.event import RuntimeEventType

            evt = {
                AgentStatus.CREATED: None,  # 创建时直接发 AGENT_CREATED
                AgentStatus.IDLE: RuntimeEventType.AGENT_IDLE,
                AgentStatus.BUSY: RuntimeEventType.AGENT_BUSY,
                AgentStatus.PAUSED: RuntimeEventType.AGENT_PAUSED,
                AgentStatus.DEGRADED: RuntimeEventType.AGENT_DEGRADED,
            }.get(to)
            if to is AgentStatus.IDLE and prev is AgentStatus.DEGRADED:
                evt = RuntimeEventType.AGENT_RECOVERED
            if evt is None:
                return
            # V1.4b: RH-03 铁律——持写事务组件埋事件同事务直插
            # （独立 EventEngine session 在并发 runner 下跨连接拖锁）
            import json as _json

            self._session.add(RuntimeEvent(
                event_type=evt.value,
                project_id=instance.project_id,
                phase="AGENT_FSM",
                message=(
                    f"Agent#{instance.id} {prev.value}->{to.value}"
                    f" ({actor}: {reason[:60]})"
                ),
                actor=actor,
                payload=_json.dumps({
                    "instance_id": instance.id,
                    "from": prev.value,
                    "to": to.value,
                    "reason": reason[:200],
                }, ensure_ascii=False),
            ))
        except Exception:  # noqa: BLE001
            logger.debug("agent lifecycle event failed", exc_info=True)


# ── 九标准环节词表（§6.1 冻结——migration seed 同源）───────────────
STANDARD_STAGES = (
    "项目理解", "需求基线", "解决方案", "原型设计", "架构设计",
    "开发实现", "测试验证", "交付打包", "使用文档", "misc",
)

# 别名表（§6.1 归一化第 2 步——seed 资产，扩词走 CR）
STAGE_ALIASES = {
    # 英文 task_type（keyword 路径/外部调用方）→ 中文环节
    "development": "开发实现",
    # 英文九阶段名（V1.4b 修3——LLM 抽取英文文档的阶段词归一）
    "project understanding": "项目理解",
    "understanding": "项目理解",
    "method identification": "项目理解",
    "requirements": "需求基线",
    "requirement baseline": "需求基线",
    "solution": "解决方案",
    "proposal": "解决方案",
    "prototype": "原型设计",
    "development design": "架构设计",
    "system design": "架构设计",
    "implementation": "开发实现",
    "worker execution": "开发实现",
    "verification": "测试验证",
    "delivery": "交付打包",
    "packaging": "交付打包",
    "user guide": "使用文档",
    "manual": "使用文档",
    "task planning": "架构设计",
    "playbook": "架构设计",
    "gate": "测试验证",
    "project completion": "使用文档",
    "coding": "开发实现",
    "testing": "测试验证",
    "test": "测试验证",
    "design": "架构设计",
    "architecture": "架构设计",
    "requirement": "需求基线",
    "analysis": "项目理解",
    "documentation": "使用文档",
    "release": "交付打包",
    "deployment": "交付打包",
    # 中文别名
    "实现开发": "开发实现",
    "需求分析": "需求基线",
    "需求文档": "需求基线",
    "系统设计": "架构设计",
    "概要设计": "架构设计",
    "详细设计": "架构设计",
    "编码": "开发实现",
    "单元测试": "测试验证",
    "集成测试": "测试验证",
    "部署": "交付打包",
    "上线": "交付打包",
    "用户文档": "使用文档",
    "default": "misc",   # 兜底词统一目标（§6.1 第 5 条）
    "generic": "misc",
}


def normalize_stage(task_type: str | None) -> str:
    """归一化三步（§6.1）: 精确等值 → 别名表 → misc。

    V1.4b: 大小写不敏感（LLM 抽取英文文档产出 Project Understanding
    等首字母大写形态——精确匹配恒 miss 落 misc）。
    """
    if not task_type:
        return "misc"
    t = task_type.strip()
    if t in STANDARD_STAGES:
        return t
    lower = t.lower()
    if lower in STANDARD_STAGES:
        return lower
    return STAGE_ALIASES.get(t) or STAGE_ALIASES.get(lower, "misc")


class WorkerRegistry:
    """Agent 实例注册与任务路由（§6.2 派发流程）。

    派发: 归一化 → stage_affinity 匹配 → IDLE 优先 → 插件需求过滤
          → 无匹配返回 None（Controller 请求创建——批次 2）
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self.fsm = AgentFSM(session)

    async def route(
        self, project_id: int, task_type: str | None,
        required_plugins: list[str] | None = None,
    ) -> AgentInstance | None:
        """任务 → 候选 Agent（IDLE 优先；DEGRADED 冷却期满自动自愈）。"""
        stage = normalize_stage(task_type)
        stmt = (
            select(AgentInstance)
            .join(AgentDefinition, AgentInstance.definition_id == AgentDefinition.id)
            .where(
                AgentInstance.project_id == project_id,
                AgentInstance.status != AgentStatus.TERMINATED,
            )
        )
        instances = list(
            (await self._session.execute(stmt)).scalars().all()
        )
        # DEGRADED 自愈尝试（冷却期满→IDLE——不影响本次路由判定）
        for inst in instances:
            if inst.status is AgentStatus.DEGRADED:
                await self.fsm.try_recover(inst)

        candidates: list[AgentInstance] = []
        for inst in instances:
            d = await self._session.get(AgentDefinition, inst.definition_id)
            if d is None or not d.enabled:
                continue
            affinity = d.stage_affinity or []
            if stage not in affinity and "misc" not in affinity:
                continue
            # 插件需求过滤（§6.2 第 3 步）
            if required_plugins:
                have = set(d.plugins or [])
                if not set(required_plugins).issubset(have):
                    continue
            candidates.append(inst)
        if not candidates:
            return None
        # IDLE 优先（CREATED 其次——都算可用；BUSY/PAUSED/DEGRADED 不派）
        idle = [c for c in candidates
                if c.status in (AgentStatus.IDLE, AgentStatus.CREATED)]
        return (idle or candidates)[0]

    async def spawn(
        self, definition_id: int, project_id: int
    ) -> AgentInstance:
        """实例化（§2.2 三路径汇合点——系统函数，非插件）。"""
        inst = AgentInstance(
            definition_id=definition_id,
            project_id=project_id,
            status=AgentStatus.CREATED,
        )
        self._session.add(inst)
        await self._session.flush()
        try:
            import json as _json

            from project_runtime.event.models import RuntimeEventType

            # V1.4b: 同事务直插（RH-03——独立 session 并发拖锁）
            self._session.add(RuntimeEvent(
                event_type=RuntimeEventType.AGENT_CREATED.value,
                project_id=project_id,
                phase="AGENT_LIFECYCLE",
                message=(
                    f"agent instance #{inst.id} created (def={definition_id})"
                ),
                actor="SYSTEM",
                payload=_json.dumps({
                    "agent_instance_id": inst.id,
                    "definition_id": definition_id,
                }, ensure_ascii=False),
            ))
        except Exception:  # noqa: BLE001
            logger.debug("agent created event failed", exc_info=True)
        return inst


# ── AgentProvisioner（§2.2 路径 1——规划后自动创建）────────────────

BUILT_IN_TEMPLATES = [
    {
        "agent_id": "analyst",
        "name": "分析智能体",
        "role_description": "负责项目理解与需求基线阶段——理解输入材料、"
                            "定义结构化需求并产出汇报材料（PPT）。",
        "plugins": ["file_rw", "web_search", "webfetch", "slide_generator"],
        "skills": None,
        "stage_affinity": ["项目理解", "需求基线"],
        "source": AgentSource.BUILT_IN,
    },
    {
        "agent_id": "designer",
        "name": "设计智能体",
        "role_description": "负责解决方案、原型设计与架构设计阶段——"
                            "产出方案文档、架构图与评审材料。",
        "plugins": ["file_rw", "web_search", "webfetch",
                    "slide_generator", "arch_diagram"],
        "skills": None,
        "stage_affinity": ["解决方案", "原型设计", "架构设计"],
        "source": AgentSource.BUILT_IN,
    },
    {
        "agent_id": "coder",
        "name": "编码智能体",
        "role_description": "负责开发实现阶段——按契约实现功能代码。",
        # V1.4: +web_search（三能力③基线）; lsp 摘除（provider 未实现）
        "plugins": ["file_rw", "web_search", "shell", "code_search", "git"],
        # V1.4: skills None=不限（[] 是 fail-closed 零 skill——违反
        # "安装即可被程序利用"定则）
        "skills": None,
        "stage_affinity": ["开发实现"],
        "source": AgentSource.BUILT_IN,
    },
    {
        "agent_id": "tester",
        "name": "测试智能体",
        "role_description": "负责测试验证阶段——编写与执行测试、产出报告。",
        "plugins": ["file_rw", "web_search", "shell", "test_runner", "code_search"],
        "skills": None,
        "stage_affinity": ["测试验证"],
        "source": AgentSource.BUILT_IN,
    },
    {
        "agent_id": "releaser",
        "name": "交付智能体",
        "role_description": "负责交付打包与使用文档阶段——构建交付物与文档。",
        "plugins": ["file_rw", "web_search", "shell", "builder", "slide_generator"],
        "skills": None,
        "stage_affinity": ["交付打包", "使用文档"],
        "source": AgentSource.BUILT_IN,
    },
]


async def seed_builtin_templates(session: AsyncSession) -> int:
    """内置五模板 seed（V1.4: BUILT_IN 来源 upsert）。

    出厂配置归代码管——BUILT_IN 行随代码更新白名单/亲和（含修订）;
    USER / CONTROLLER_GENERATED 来源的行永不覆盖（用户与 AI 的
    定制不受 seed 干扰）。幂等: 连续 seed 同值零副作用。
    返回插入数（upsert 更新不计数）。
    """
    from project_runtime.worker.agent_models import AgentSource

    count = 0
    for tpl in BUILT_IN_TEMPLATES:
        exists = (
            await session.execute(
                select(AgentDefinition).where(
                    AgentDefinition.agent_id == tpl["agent_id"]
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            if exists.source is AgentSource.BUILT_IN:
                # upsert: 同步最新出厂配置（plugins/skills/亲和/描述）
                exists.plugins = tpl["plugins"]
                exists.skills = tpl["skills"]
                exists.stage_affinity = tpl["stage_affinity"]
                exists.role_description = tpl["role_description"]
            continue  # USER/CONTROLLER_GENERATED 永不覆盖
        session.add(AgentDefinition(
            agent_id=tpl["agent_id"],
            name=tpl["name"],
            role_description=tpl["role_description"],
            plugins=tpl["plugins"],
            skills=tpl["skills"],
            stage_affinity=tpl["stage_affinity"],
            source=tpl["source"],
        ))
        count += 1
    await session.flush()
    return count


class AgentProvisioner:
    """规划完成后自动创建项目 Agent（§2.2 路径 1 主路径）。

    按任务图环节归组匹配模板 → 缺模板环节用 misc 亲和模板兜底
    → 批量实例化；无任务的环节不创建。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._registry = WorkerRegistry(session)

    async def provision(
        self, project_id: int, task_types: list[str | None]
    ) -> list[AgentInstance]:
        """task_types = 任务图的 task_type 集合。

        V1.4b 修2: misc 不再丢弃——无模板环节（含 misc）走
        Controller 派生链兜底（ensure_controller_template 含双闸:
        签名幂等 + 数量 ≤ 环节数+2——"防乱建"由闸承接不靠丢弃）。
        """
        # 归一化 → 环节集合（保留 misc——统一派生兜底）
        stages = {normalize_stage(t) for t in task_types}
        if not stages:
            return []
        # 环节 → 模板（匹配 stage_affinity）
        defs = list(
            (
                await self._session.execute(
                    select(AgentDefinition).where(
                        AgentDefinition.enabled.is_(True)
                    )
                )
            ).scalars().all()
        )
        spawned: list[AgentInstance] = []
        for stage in stages:
            d = next(
                (d for d in defs
                 if d.stage_affinity and stage in d.stage_affinity),
                None,
            )
            if d is None:
                # V1.4b 修2: 无模板环节 → Controller 派生链兜底
                # （misc/新环节——基线 or 环节上限推导, 双闸防爆炸）
                try:
                    from project_runtime.worker.derive import (
                        ensure_controller_template,
                    )

                    d = await ensure_controller_template(
                        self._session, project_id, stage, None
                    )
                except Exception:  # noqa: BLE001 — 派生失败跳过该环节
                    logger.debug(
                        "derive fallback for stage %s failed",
                        stage, exc_info=True,
                    )
                if d is None:
                    logger.info(
                        "no template for stage %s — skip", stage
                    )
                    continue
            # 已有活跃实例则复用（同模板同项目唯一）
            existing = (
                await self._session.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == project_id,
                        AgentInstance.definition_id == d.id,
                        AgentInstance.status != AgentStatus.TERMINATED,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            inst = await self._registry.spawn(d.id, project_id)
            spawned.append(inst)
        return spawned
