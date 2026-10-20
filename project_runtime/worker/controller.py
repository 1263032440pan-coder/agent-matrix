"""Controller 智能体（20号规范 §3.4——既有规划职能 Agent 化 + 新增管理）.

批次 2 交付四件:
  ① 规划链路 Agent 化（职能不变，主体升级——双轨保险）
  ② 偏移巡检（Gate 语义复用——契约对照）+ correction 纠正闭环
  ③ 交付清单对账循环（§3.4 长文档续作解法——检测/决策/驱动）
  ④ 防死循环五闸（§3.4——有限可判定保证）

红线:
  - Controller 不执行业务任务（不写代码/测试）
  - 检测依据 = 自建契约（CompletionCheck 七条件语义）
  - 五闸: 验收冻结（REWORK 指令必须引用条款）/ REWORK 上限 3 /
    补给预算 50% / 全局熔断（连续 8 FAIL/REWORK→项目 PAUSED）/
    成本可见（事件）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.acp import AcpBus, AcpMsg
from project_runtime.worker.agent_models import (
    AgentInstance,
    AgentStatus,
)

logger = logging.getLogger(__name__)

# ── 防死循环五闸参数（§3.4——旋钮只归用户，不归 LLM）──────────────
MAX_REWORK_ROUNDS = 3
SUPPLY_BUDGET_RATIO = 0.5          # 补给 ≤ 原节点数 50%
GLOBAL_FUSE_THRESHOLD = 8          # 连续 8 FAIL/REWORK → PAUSED

# REWORK 指令合法性: 必须引用具体条款（禁开放式挑刺——五闸①）
_CLAUSE_REF = re.compile(r"条款\s*\d+|违反\s*\d+|[Ff][Rr]-\d+")


class OpenEndedReworkError(Exception):
    """五闸①: REWORK 指令未引用具体条款——开放式挑刺被拒。"""


class ReworkLimitExceededError(Exception):
    """五闸②: REWORK 达上限——升级 ManualAction。"""


@dataclass
class ReconciliationReport:
    """对账结果（manifest_check 事件 payload）。"""

    project_id: int
    round_no: int = 1
    covered: list[str] = field(default_factory=list)     # ✓ 清单项有任务且 ACCEPTED
    stuck: list[str] = field(default_factory=list)       # ✗ 卡点（FAILED/BLOCKED）
    missing: list[str] = field(default_factory=list)     # ✗ 漏项（无任务覆盖）
    all_green: bool = False
    supply_used: int = 0
    supply_budget: int = 0


class Controller:
    """管理型 Agent——规划链路 Agent 化 + 巡检/纠正/对账/调度。

    批次 2 形态: 事件驱动的服务对象（无独立 LLM 循环——决策全部
    确定性规则: 契约对照/Traceability 覆盖/CompletionCheck 语义）。
    Controller 的"思考"体现在规划链（既有 /plan 全链）与纠正指令的
    构造——它们本身走 LLM；本类不新增 LLM 调用点（零隐式成本）。
    """

    def __init__(self, session: AsyncSession, event_engine=None):
        self._session = session
        self.bus = AcpBus(session)
        # EventEngine 可注入（测试隔离）；None=全局引擎（生产）
        self._event_engine = event_engine

    # ═══ ② 偏移巡检 + correction 纠正闭环 ═══════════════════════

    async def inspect_and_correct(
        self, project_id: int, instance_id: int, task_id: int,
        contract: str, output: str,
    ) -> int | None:
        """对照契约检测产出偏移 → 发 correction（先 PAUSE 再指令）。

        契约对照（Gate 语义简化版）: 契约中的验收关键词 vs 产出命中。
        真实 Gate 判定仍由 GateCore 负责——本方法做巡检级快速对照，
        发现疑似偏移即纠正（宁可多查一次 Gate，不可放过偏移）。
        返回 AcpMessage id；无偏移返回 None。
        """
        keywords = self._contract_keywords(contract)
        if not keywords:
            return None
        missing = [k for k in keywords if k not in (output or "")]
        if not missing:
            return None  # 全命中——无偏移
        # 纠正闭环: PAUSE → correction（引用契约条款语义）→ 目标 Agent 重启
        await self._pause_agent(instance_id)
        msg_id = await self.bus.send(AcpMsg(
            msg_type="correction",
            project_id=project_id,
            to_agent_instance_id=instance_id,
            task_id=task_id,
            payload={
                "kind": "output_deviation",
                "missing_keywords": missing[:10],
                "instruction": (
                    f"产出与契约存在偏移——以下验收要点未体现: "
                    f"{'、'.join(missing[:5])}。请按任务契约重做当前任务。"
                ),
            },
        ))
        return msg_id

    @staticmethod
    def _contract_keywords(contract: str) -> list[str]:
        """契约关键词提取（巡检级——非 Gate 正判）。"""
        if not contract:
            return []
        try:
            data = json.loads(contract) if contract.strip().startswith("{") else None
        except ValueError:
            data = None
        text_parts: list[str] = []
        if data:
            for v in (data.get("acceptance") or data.get("acceptance_criteria")
                      or []):
                if isinstance(v, str):
                    text_parts.append(v)
            if data.get("description"):
                text_parts.append(str(data["description"]))
        if not text_parts:
            text_parts = [contract]
        # 关键词 = 2 字以上中文词/英文词（粗粒度——巡检用）
        # 剔除契约结构性词汇（验收/条款等——元语言不是验收内容）
        _structural_words = {"验收", "条款", "要求", "描述", "标准", "契约",
                       "实现", "完成", "输出", "包含"}
        kws: list[str] = []
        for part in text_parts:
            for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", part):
                if token in _structural_words:
                    continue
                if token not in kws and len(token) >= 2:
                    kws.append(token)
        return kws[:15]

    # ═══ ③ 交付清单对账循环 ═════════════════════════════════════

    async def reconcile(
        self, project_id: int,
    ) -> ReconciliationReport:
        """清单对账: 任务图 vs 交付清单（契约集）——检测/决策/驱动。

        复用既有资产:
        - 清单原子项 = Task.contract（Controller 规划期产出）
        - 覆盖判定 = 任务状态 + 产物存在（CompletionCheck 语义简化版）
        - 漏项 = 契约集里有、任务图里没有的项（长文档遗忘检测）

        停止条件语义（§3.4 驱动层）: all_green=False 时 FULL_AUTO 不宣布
        完成——调用方（规划链/scheduler）据 report 决定补任务/纠正。
        """
        from project_runtime.project_state.enums import TaskStatus
        from project_runtime.project_state.models import Task

        stmt = select(Task).where(Task.project_id == project_id).order_by(Task.id)
        tasks = list((await self._session.execute(stmt)).scalars().all())

        report = ReconciliationReport(project_id=project_id)
        report.supply_budget = max(1, int(len(tasks) * SUPPLY_BUDGET_RATIO))

        covered: list[str] = []
        stuck: list[str] = []
        for t in tasks:
            name = t.name
            if t.status is TaskStatus.ACCEPTED:
                covered.append(name)
            elif t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
                stuck.append(name)
            # CREATED/READY/RUNNING/EXECUTION_DONE/REWORK 属进行中——不计三类
        report.covered = covered
        report.stuck = stuck

        # 漏项检测: 契约集 vs 任务图——契约是按任务存的，V0.1 的清单
        # 原子项即任务本身；真正的"文档 FR 级漏项"需对照方法资产原文
        # （批次 3 接 Traceability rebuild_scope 后按 requirement 节点
        #  对照）。当前层: 检查任务图中 REWORK 循环停滞（死循环信号）
        report.missing = []  # 批次 3: Traceability requirement 对照
        report.all_green = (
            not stuck and not report.missing
            and len(covered) == len(tasks)
            and len(tasks) > 0
        )

        # manifest_check 审计事件
        await self._emit_manifest_check(report)
        return report

    # ═══ ④ 防死循环五闸 ═════════════════════════════════════════

    @staticmethod
    def validate_rework_instruction(instruction: str) -> None:
        """五闸①: REWORK 指令必须引用具体条款——开放式挑刺拒绝。"""
        if not _CLAUSE_REF.search(instruction or ""):
            raise OpenEndedReworkError(
                f"REWORK 指令必须引用具体条款（如'违反条款 3'/'FR-003'），"
                f"禁止开放式挑刺（如'测试不够充分'）: {instruction[:60]}"
            )

    @staticmethod
    def check_rework_limit(task_name: str, rework_round: int) -> None:
        """五闸②: 达上限抛异常——调用方升级 ManualAction 三选一。"""
        if rework_round >= MAX_REWORK_ROUNDS:
            raise ReworkLimitExceededError(
                f"任务「{task_name}」REWORK 达上限 {MAX_REWORK_ROUNDS} 轮——"
                f"升级 ManualAction（WAIVED 降级/换 Agent/用户裁决）"
            )

    @staticmethod
    def check_supply_budget(
        original_count: int, supplied: int
    ) -> None:
        """五闸③: 补给超预算抛异常——停止补给升级用户。"""
        budget = max(1, int(original_count * SUPPLY_BUDGET_RATIO))
        if supplied >= budget:
            raise ReworkLimitExceededError(
                f"漏项补给达预算上限 {supplied}/{budget}"
                f"（原节点 {original_count} 的 50%）——"
                f"文档过大/需求颗粒度不适配，建议拆项目或人工规划"
            )

    async def check_global_fuse(self, project_id: int) -> bool:
        """五闸④: 连续 N 个任务终态均 FAIL/REWORK → 项目 PAUSED。

        返回 True = 已熔断（调用方停止一切派发）。
        """
        from project_runtime.project_state.enums import ProjectStatus, TaskStatus
        from project_runtime.project_state.models import Project, Task

        # updated_at 同刻（批量 seed）需 tie-break——id desc 保证
        # "最近"语义确定（无 ORDER BY 稳定性的静默错配坑）
        stmt = (
            select(Task.status)
            .where(Task.project_id == project_id)
            .order_by(Task.updated_at.desc(), Task.id.desc())
            .limit(GLOBAL_FUSE_THRESHOLD)
        )
        # scalars() 对单列 select 已返回标量列表（enum 对象）——
        # 勿再 row[0]（str enum 下标取的是首字符 'F'——静默错配的坑）
        recent = list((await self._session.execute(stmt)).scalars().all())
        bad = {TaskStatus.FAILED, TaskStatus.REWORK}
        if (
            len(recent) >= GLOBAL_FUSE_THRESHOLD
            and all(s in bad for s in recent)
        ):
            proj = await self._session.get(Project, project_id)
            if proj is not None and proj.status is not ProjectStatus.PAUSED:
                proj.status = ProjectStatus.PAUSED
                await self._session.flush()
                logger.error(
                    "GLOBAL FUSE: project %d 连续 %d 次 FAIL/REWORK → PAUSED",
                    project_id, len(recent),
                )
            return True
        return False

    # ═══ 驱动: 对账后决策（§3.4 四动作——批次 2 接通 REWORK/卡点）═══

    async def act_on_report(
        self, report: ReconciliationReport,
    ) -> list[int]:
        """对账四动作: 卡点→correction / 全绿→放行。

        漏项补任务（第五触发源 RECONCILIATION_GAP）批次 2 先登记
        事件（真实建任务经 Replanner.replan 主链——批次 3 接线，
        本方法返回 pending_missing 供上层调用）。
        """
        msg_ids: list[int] = []
        # 卡点: FAILED/BLOCKED 任务 → 对其执行 Agent 发 correction
        if report.stuck:
            from project_runtime.project_state.models import Task

            for name in report.stuck:
                t = (
                    await self._session.execute(
                        select(Task).where(
                            Task.project_id == report.project_id,
                            Task.name == name,
                        )
                    )
                ).scalar_one_or_none()
                if t is None:
                    continue
                inst = await self._instance_for_task(t.id)
                if inst is None:
                    continue
                mid = await self.bus.send(AcpMsg(
                    msg_type="correction",
                    project_id=report.project_id,
                    to_agent_instance_id=inst.id,
                    task_id=t.id,
                    payload={
                        "kind": "stuck_task",
                        "instruction": (
                            f"任务「{name}」处于 {t.status.value}——"
                            f"请按任务契约恢复执行；无法恢复则上报 report。"
                        ),
                    },
                ))
                msg_ids.append(mid)
        return msg_ids

    # ═══ 内部 ═══════════════════════════════════════════════════

    async def _pause_agent(self, instance_id: int) -> None:
        from project_runtime.worker.registry import (
            AgentFSM,
            IllegalAgentTransitionError,
        )

        inst = await self._session.get(AgentInstance, instance_id)
        if inst is None:
            return
        try:
            await AgentFSM(self._session).transition(
                inst, AgentStatus.PAUSED, actor="CONTROLLER",
                reason="deviation correction",
            )
        except IllegalAgentTransitionError:
            pass  # BUSY/IDLE 外的态跳过（PAUSED 已是/TERMINATED 不可）

    async def _instance_for_task(
        self, task_id: int
    ) -> AgentInstance | None:
        stmt = select(AgentInstance).where(
            AgentInstance.current_task_id == task_id,
            AgentInstance.status != AgentStatus.TERMINATED,
        )
        return (
            await self._session.execute(stmt)
        ).scalar_one_or_none()

    async def _emit_manifest_check(self, r: ReconciliationReport) -> None:
        """manifest_check 审计事件（§5——对账可观测信号）。"""
        try:
            from project_runtime.event import EventEngine
            from project_runtime.event.models import RuntimeEventType

            eng_ = self._event_engine or EventEngine()
            await eng_.record(
                RuntimeEventType.MANIFEST_CHECK,
                project_id=r.project_id,
                phase="CONTROLLER",
                message=(
                    f"对账第{r.round_no}轮: ✓{len(r.covered)} "
                    f"✗卡{len(r.stuck)} ✗漏{len(r.missing)}"
                    + (" [ALL GREEN]" if r.all_green else "")
                ),
                actor="CONTROLLER",
                payload={
                    "round": r.round_no,
                    "covered": r.covered[:50],
                    "stuck": r.stuck[:20],
                    "missing": r.missing[:20],
                    "all_green": r.all_green,
                    "supply_used": r.supply_used,
                    "supply_budget": r.supply_budget,
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("manifest_check event failed", exc_info=True)
