"""执行链 ↔ AgentInstance 生命周期绑定（20号规范 §3——FSM 接线层）.

背景: 六态 FSM/Provisioner/路由在批次 1-3 交付但零接线（"组件交付
了接线没做"）——agent_instance 表在真实项目里永远空、FSM 无人驱动。

本模块是执行链（ExecutionRunner.run）的 Agent 生命周期挂钩，
全部 best-effort（Agent 面故障绝不阻断任务执行——降级语义与
19号"能力缺席不影响任务继续"一致）:

  派发前  bind_agent()      route 选实例 → CREATED/IDLE→BUSY + current_task_id
  成功后  release_agent()   BUSY→IDLE（清 current_task_id; CostLedger 记账）
  失败后  fail_agent()      record_failure（3 连败→DEGRADED）→ 快照 → 传播
                             （备胎转派/升级 block）
  未选上  无实例照常执行    （老路径零回归——Agent 面是增强不是依赖）

义务驱动（2026-11）:
  bind 后 ensure_capability_binding()——缺口则改绑/派生补齐/BLOCKED，
  禁止带着缺能力进工具空转。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.agent_models import AgentInstance, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class AgentBinding:
    """一次派发的 Agent 绑定结果（调用方持有，结束时回传）。"""

    instance_id: int
    project_id: int
    task_id: int
    # 返修-20号-V1.4: 白名单快照（bind 时从 definition 取——
    # commit 后 ORM 过期，快照是唯一安全通道）
    # None=不限（全部已注册可见）; list=白名单（fail-closed）
    tool_whitelist: list[str] | None = None
    skill_whitelist: list[str] | None = None


@dataclass
class CapabilityGateResult:
    """执行前门禁结果。"""

    binding: AgentBinding | None
    blocked: bool = False
    gap: set[str] = field(default_factory=set)
    reason: str = ""
    rebound: bool = False


async def bind_agent(
    session: AsyncSession, project_id: int, task_id: int,
    task_type: str | None,
    task_contract: str | None = None,
    *,
    task_content: str = "",
) -> AgentBinding | None:
    """任务派发前: 路由选 Agent 实例 → BUSY。

    无可用实例（未 provision/全忙/全 DEGRADED）返回 None——
    调用方照常走无 Agent 老路径（零回归语义）。
    """
    try:
        from project_runtime.worker.registry import WorkerRegistry

        reg = WorkerRegistry(session)
        # 返修V3 批A: 语义派活上下文（契约+显式内容）
        content = (task_content or task_contract or "")[:4000]
        inst = await reg.route(
            project_id, task_type, task_content=content,
        )
        if inst is None:
            # V1.4 §2.2 route-miss 派生链: 先尝试 Controller 派生模板
            # （双闸: 签名幂等 + 数量上限; 失败落老路径零回归）
            try:
                from project_runtime.worker.derive import (
                    ensure_controller_template,
                )

                d = await ensure_controller_template(
                    session, project_id, task_type or "",
                    task_contract,
                )
                if d is not None:
                    from project_runtime.worker.registry import (
                        AgentProvisioner,
                    )

                    await AgentProvisioner(session).provision(
                        project_id, [task_type]
                    )
                    inst = await reg.route(
                        project_id, task_type, task_content=content,
                    )
            except Exception:  # noqa: BLE001 — 派生失败落老路径
                logger.debug(
                    "controller derive on route-miss failed",
                    exc_info=True,
                )
            if inst is None:
                return None
        if inst.status is AgentStatus.CREATED:
            await reg.fsm.transition(
                inst, AgentStatus.IDLE, actor="SYSTEM", reason="first bind"
            )
        await reg.fsm.transition(
            inst, AgentStatus.BUSY, actor="SYSTEM",
            reason=f"task {task_id} dispatched",
        )
        inst.current_task_id = task_id
        # V1.4: 白名单快照（definition 归一——plugins 存裸名）
        from project_runtime.worker.agent_models import AgentDefinition

        d = await session.get(AgentDefinition, inst.definition_id)
        await session.flush()
        return AgentBinding(
            instance_id=inst.id, project_id=project_id, task_id=task_id,
            tool_whitelist=(d.plugins if d else None),
            skill_whitelist=(d.skills if d else None),
        )
    except Exception:  # noqa: BLE001 — Agent 面绝不阻断执行
        logger.debug("bind_agent failed", exc_info=True)
        return None


async def release_agent(
    session: AsyncSession, binding: AgentBinding,
    tokens_in: int = 0, tokens_out: int = 0,
) -> None:
    """任务成功后: BUSY→IDLE + 成本记账。"""
    try:
        from project_runtime.worker.cost_ledger import (
            CostEntry,
            CostLedger,
        )
        from project_runtime.worker.registry import AgentFSM

        inst = await session.get(AgentInstance, binding.instance_id)
        if inst is None:
            return
        # 记账（预算耗尽不炸任务——CostLedger 内部抛错由调用方降级）
        try:
            ledger = CostLedger(session)
            await ledger.record(CostEntry(
                binding.instance_id,
                tokens_in=tokens_in, tokens_out=tokens_out,
            ))
        except Exception:  # noqa: BLE001
            logger.debug("cost record failed", exc_info=True)
        if inst.status is AgentStatus.BUSY:
            await AgentFSM(session).transition(
                inst, AgentStatus.IDLE, actor="SYSTEM",
                reason=f"task {binding.task_id} done",
            )
        inst.current_task_id = None
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.debug("release_agent failed", exc_info=True)


async def fail_agent(
    session: AsyncSession, binding: AgentBinding, reason: str = "task failed"
) -> None:
    """任务失败后: 计数降级 + DEGRADED 快照 + 失败传播。

    3 连败 → DEGRADED（60s 冷却）→ 强制中途快照（V1.3 P2-3）→
    FailurePropagator 三段链（wait/redispatch/block）。
    """
    try:
        from project_runtime.worker.registry import AgentFSM
        from project_runtime.worker.resilience import (
            FailurePropagator,
            snapshot_agent_session,
        )

        inst = await session.get(AgentInstance, binding.instance_id)
        if inst is None:
            return
        if inst.status is AgentStatus.BUSY:
            await AgentFSM(session).transition(
                inst, AgentStatus.IDLE, actor="SYSTEM",
                reason=f"task {binding.task_id} failed",
            )
        inst.current_task_id = None
        # 失败计数（达阈值转 DEGRADED——record_failure 内部处理）
        fsm = AgentFSM(session)
        await fsm.record_failure(inst)
        if inst.status is AgentStatus.DEGRADED:
            # 强制中途快照（V1.3 P2-3: DEGRADED 必快照——接班说明闭环）
            await snapshot_agent_session(
                session, inst.id, reason="DEGRADED on failures"
            )
            prop = FailurePropagator(session)
            decision = await prop.on_agent_degraded(
                inst.project_id, inst.id, binding.task_id
            )
            logger.warning(
                "Agent#%s DEGRADED — propagation: %s", inst.id, decision
            )
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.debug("fail_agent failed", exc_info=True)


async def provision_after_plan(
    session: AsyncSession, project_id: int, task_types: list[str | None]
) -> int:
    """/plan 落账后: 按任务图环节建实例（§2.2 路径 1 主路径）。

    幂等（同模板同项目唯一活跃实例索引兜底）; 返回新建数。
    """
    try:
        from project_runtime.worker.registry import AgentProvisioner

        spawned = await AgentProvisioner(session).provision(
            project_id, task_types
        )
        await session.flush()
        return len(spawned)
    except Exception:  # noqa: BLE001 — Agent 面绝不阻断规划
        logger.debug("provision_after_plan failed", exc_info=True)
        return 0


def _plugins_cover(have: list[str] | None, need: set[str]) -> bool:
    from project_runtime.worker.obligation_plugins import plugin_gap

    return not plugin_gap(need, have)


async def _find_covering_instance(
    session: AsyncSession,
    project_id: int,
    need: set[str],
    *,
    exclude_id: int | None = None,
) -> AgentInstance | None:
    """同项目 IDLE/CREATED 且 plugins ⊇ need 的实例。"""
    from sqlalchemy import select

    from project_runtime.worker.agent_models import AgentDefinition

    rows = (
        await session.execute(
            select(AgentInstance, AgentDefinition)
            .join(
                AgentDefinition,
                AgentInstance.definition_id == AgentDefinition.id,
            )
            .where(
                AgentInstance.project_id == project_id,
                AgentInstance.status.in_([
                    AgentStatus.IDLE, AgentStatus.CREATED,
                ]),
            )
        )
    ).all()
    for inst, dfn in rows:
        if exclude_id is not None and inst.id == exclude_id:
            continue
        if _plugins_cover(dfn.plugins, need):
            return inst
    return None


async def _release_busy_quietly(
    session: AsyncSession, binding: AgentBinding,
) -> None:
    from project_runtime.worker.registry import AgentFSM

    inst = await session.get(AgentInstance, binding.instance_id)
    if inst is None:
        return
    if inst.status is AgentStatus.BUSY:
        await AgentFSM(session).transition(
            inst, AgentStatus.IDLE, actor="SYSTEM",
            reason="capability rebind",
        )
    inst.current_task_id = None
    await session.flush()


async def _bind_instance(
    session: AsyncSession,
    inst: AgentInstance,
    project_id: int,
    task_id: int,
) -> AgentBinding:
    from project_runtime.worker.agent_models import AgentDefinition
    from project_runtime.worker.registry import AgentFSM

    fsm = AgentFSM(session)
    if inst.status is AgentStatus.CREATED:
        await fsm.transition(
            inst, AgentStatus.IDLE, actor="SYSTEM", reason="capability bind",
        )
    await fsm.transition(
        inst, AgentStatus.BUSY, actor="SYSTEM",
        reason=f"task {task_id} capability bind",
    )
    inst.current_task_id = task_id
    d = await session.get(AgentDefinition, inst.definition_id)
    await session.flush()
    return AgentBinding(
        instance_id=inst.id,
        project_id=project_id,
        task_id=task_id,
        tool_whitelist=(d.plugins if d else None),
        skill_whitelist=(d.skills if d else None),
    )


async def ensure_capability_binding(
    session: AsyncSession,
    project_id: int,
    task_id: int,
    binding: AgentBinding | None,
    *,
    playbook_ref: str | None,
    manifest: dict | None = None,
    task_type: str | None = None,
    task_contract: str | None = None,
) -> CapabilityGateResult:
    """义务驱动执行前门禁：改绑 → SAFE_INCREMENT 派生补齐 → BLOCKED。

    binding=None（无 Agent 老路径）且 need 非空时仍尝试派生绑定；
    派生失败则不阻断（老路径工具面不限——与既有零回归一致）。
    """
    from project_runtime.worker.obligation_plugins import (
        contract_hint_for_plugins,
        plugin_gap,
        required_plugins_for_playbook_ref,
    )

    need = required_plugins_for_playbook_ref(
        playbook_ref, manifest=manifest,
    )
    if not need:
        return CapabilityGateResult(binding=binding)

    have = binding.tool_whitelist if binding is not None else None
    gap = plugin_gap(need, have)
    if not gap:
        return CapabilityGateResult(binding=binding)

    # 1) 改绑到已有覆盖实例
    try:
        alt = await _find_covering_instance(
            session, project_id, need,
            exclude_id=binding.instance_id if binding else None,
        )
        if alt is not None:
            if binding is not None:
                await _release_busy_quietly(session, binding)
            new_b = await _bind_instance(session, alt, project_id, task_id)
            return CapabilityGateResult(
                binding=new_b, rebound=True, gap=set(),
                reason=f"rebound to instance#{alt.id}",
            )
    except Exception:  # noqa: BLE001
        logger.debug("capability rebind scan failed", exc_info=True)

    # 2) Controller 派生 + 补齐 SAFE_INCREMENT，再绑定
    try:
        from project_runtime.worker.derive import (
            SAFE_INCREMENT_PLUGINS,
            ensure_controller_template,
        )
        from project_runtime.worker.registry import AgentProvisioner

        if gap <= SAFE_INCREMENT_PLUGINS:
            aug = contract_hint_for_plugins(need, task_contract)
            d = await ensure_controller_template(
                session, project_id, task_type or "misc", aug,
            )
            if d is not None:
                # 强制补齐缺口（派生词可能漏扫）
                plugs = list(d.plugins or [])
                changed = False
                for p in gap:
                    if p in SAFE_INCREMENT_PLUGINS and p not in plugs:
                        plugs.append(p)
                        changed = True
                if changed:
                    d.plugins = plugs
                    await session.flush()
                await AgentProvisioner(session).provision(
                    project_id, [task_type or "misc"],
                )
                alt = await _find_covering_instance(
                    session, project_id, need,
                    exclude_id=binding.instance_id if binding else None,
                )
                # 也可能刚建的实例仍是 CREATED；再找含本定义的
                if alt is None:
                    from sqlalchemy import select


                    rows = (
                        await session.execute(
                            select(AgentInstance).where(
                                AgentInstance.project_id == project_id,
                                AgentInstance.definition_id == d.id,
                                AgentInstance.status != AgentStatus.TERMINATED,
                            )
                        )
                    ).scalars().all()
                    for inst in rows:
                        if _plugins_cover(plugs, need):
                            alt = inst
                            break
                if alt is not None:
                    if binding is not None:
                        await _release_busy_quietly(session, binding)
                    new_b = await _bind_instance(
                        session, alt, project_id, task_id,
                    )
                    return CapabilityGateResult(
                        binding=new_b, rebound=True, gap=set(),
                        reason=f"derived+bound instance#{alt.id}",
                    )
    except Exception:  # noqa: BLE001
        logger.debug("capability derive fill failed", exc_info=True)

    # 3) 有白名单缺口且无法补齐 → BLOCKED（禁止空转）
    # 无 binding（老路径不限工具）→ 不阻断
    if binding is None and have is None:
        return CapabilityGateResult(
            binding=None, gap=gap,
            reason="no agent; legacy unlimited tools",
        )

    try:
        from project_runtime.project_state.enums import TaskStatus
        from project_runtime.project_state.fsm import StateTransitionService
        from project_runtime.project_state.models import Task

        task = await session.get(Task, task_id)
        if task is not None and task.status in (
            TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.CREATED,
        ):
            if binding is not None:
                await _release_busy_quietly(session, binding)
            await StateTransitionService(session).transition_task(
                task, TaskStatus.BLOCKED,
            )
            await session.flush()
    except Exception:  # noqa: BLE001
        logger.warning(
            "capability BLOCKED transition failed task=%s gap=%s",
            task_id, sorted(gap), exc_info=True,
        )
        return CapabilityGateResult(
            binding=binding, blocked=True, gap=gap,
            reason=f"capability gap {sorted(gap)} (transition failed)",
        )

    return CapabilityGateResult(
        binding=None, blocked=True, gap=gap,
        reason=f"capability gap: {', '.join(sorted(gap))}",
    )
