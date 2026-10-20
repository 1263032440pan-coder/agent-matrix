"""成本账本（20号规范 §10.3 批次 3——每 Agent token/调用预算与统计）.

纪律:
  - 预算 0 = 不限（默认——Demo V0.1 不强制）
  - 记账在 LLM 调用返回后（usage 透传——缺 usage 时按 token 估算）
  - 超预算不炸任务: 抛 BudgetExceededError 由调用方决定降级
    （Agent 转 DEGRADED / 任务 BLOCKED——五闸语义之外的成本闸）
  - 统计查询给 UI（成本可见——五闸⑤）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.agent_models import AgentInstance

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Agent token 预算耗尽（预算 >0 且 used+delta > budget）。"""


@dataclass
class CostEntry:
    """一次 LLM 调用的成本计量。"""

    agent_instance_id: int
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 1

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


class CostLedger:
    """记账 + 预算检查（Agent 实例行内累计——无独立表，Demo 取简）。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def record(self, entry: CostEntry) -> int:
        """累加统计; 返回累计 token_used。预算耗尽抛 BudgetExceededError。"""
        inst = await self._session.get(AgentInstance, entry.agent_instance_id)
        if inst is None:
            return 0
        inst.token_used += entry.total
        inst.call_count += entry.calls
        await self._session.flush()
        if inst.token_budget and inst.token_used > inst.token_budget:
            raise BudgetExceededError(
                f"Agent#{inst.id} 预算耗尽: "
                f"{inst.token_used}/{inst.token_budget} tokens"
            )
        return inst.token_used

    @staticmethod
    def usage_from_response(resp: dict) -> tuple[int, int]:
        """从 LiteLLM 响应提取 usage（缺省 0——不估算冒充真实）。"""
        usage = (resp or {}).get("usage") or {}
        return int(usage.get("prompt_tokens", 0)), int(
            usage.get("completion_tokens", 0)
        )

    async def summary(self, project_id: int) -> list[dict]:
        """项目内各 Agent 成本汇总（UI 成本可见）。"""
        stmt = select(AgentInstance).where(
            AgentInstance.project_id == project_id
        )
        out = []
        for inst in (await self._session.execute(stmt)).scalars():
            out.append({
                "agent_instance_id": inst.id,
                "token_used": inst.token_used,
                "token_budget": inst.token_budget,
                "call_count": inst.call_count,
                "over_budget": bool(
                    inst.token_budget and inst.token_used > inst.token_budget
                ),
            })
        return out
