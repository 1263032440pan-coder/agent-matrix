"""ACP 消息总线（20号规范 §5——五类消息 + 超时升级）.

消息类型: request/inform（Agent↔Agent）/ correction（Controller→Agent）/
          report（Agent→Controller）/ directive（Controller→Agent）
纪律: 每条消息对应 TaskDependency 上的具体交接物或裁决——不做自由聊天。
request 带 120s 超时，超时升级 report→Controller。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.worker.agent_models import AcpMessage

logger = logging.getLogger(__name__)

MSG_TYPES = ("request", "inform", "correction", "report", "directive")
REQUEST_TIMEOUT_S = 120


class AcpError(Exception):
    pass


@dataclass
class AcpMsg:
    """五类消息之一（进程内传递形态——落库 AcpMessage 同字段）。"""

    msg_type: str
    project_id: int
    payload: dict = field(default_factory=dict)
    from_agent_instance_id: int | None = None
    to_agent_instance_id: int | None = None
    task_id: int | None = None


class AcpBus:
    """ACP 消息总线——发消息（落库审计）+ 等待应答（request 超时升级）。

    V0.1 形态: 消息即时投递到进程内收件箱（dict[int, list[AcpMsg]]），
    落库 AcpMessage 持久化。跨进程/远端形态属 V1（§10 Out of Scope）。
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        # 进程内收件箱（instance_id → 未读消息）
        self._inboxes: dict[int, list[AcpMsg]] = {}

    async def send(self, msg: AcpMsg) -> int:
        """发消息——校验类型 + 落库审计 + 进程内投递。"""
        if msg.msg_type not in MSG_TYPES:
            raise AcpError(f"未知消息类型: {msg.msg_type}（五类: {MSG_TYPES}）")
        row = AcpMessage(
            project_id=msg.project_id,
            msg_type=msg.msg_type,
            from_agent_instance_id=msg.from_agent_instance_id,
            to_agent_instance_id=msg.to_agent_instance_id,
            task_id=msg.task_id,
            payload=json.dumps(msg.payload, ensure_ascii=False),
        )
        self._session.add(row)
        await self._session.flush()
        if msg.to_agent_instance_id is not None:
            self._inboxes.setdefault(msg.to_agent_instance_id, []).append(msg)
        return row.id

    def poll(self, instance_id: int) -> list[AcpMsg]:
        """取走该 Agent 的未读消息（清空收件箱）。"""
        msgs = self._inboxes.get(instance_id, [])
        self._inboxes[instance_id] = []
        return list(msgs)

    async def request(self, msg: AcpMsg) -> AcpMsg | None:
        """request 语义: 发出 + 等待对端 inform 应答（超时升级）。

        V0.1: 对端应答由 Controller 调度循环 poll 后回 inform；
        本方法轮询收件箱至超时。超时 → 自动发 report 升级 Controller。
        """
        if msg.msg_type != "request":
            raise AcpError("request() 只发 request 类型")
        await self.send(msg)
        if msg.to_agent_instance_id is None:
            return None
        deadline = time.monotonic() + REQUEST_TIMEOUT_S
        while time.monotonic() < deadline:
            incoming = [
                m for m in self.poll(msg.from_agent_instance_id or -1)
                if m.msg_type == "inform"
                and m.from_agent_instance_id == msg.to_agent_instance_id
            ]
            if incoming:
                return incoming[0]
            await _sleep(0.2)
        # 超时升级（§5.4: request 超时 → report Controller）
        await self.send(AcpMsg(
            msg_type="report",
            project_id=msg.project_id,
            from_agent_instance_id=msg.from_agent_instance_id,
            payload={
                "kind": "request_timeout",
                "original_request": msg.payload,
                "to": msg.to_agent_instance_id,
            },
            task_id=msg.task_id,
        ))
        return None


async def _sleep(s: float) -> None:
    import asyncio

    await asyncio.sleep(s)
