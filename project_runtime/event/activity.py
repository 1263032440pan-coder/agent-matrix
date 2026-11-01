"""运行动态 AGENT_ACTIVITY 短写入（best-effort，节流防刷屏）。"""

from __future__ import annotations

import time
from typing import Any

# phase → 最近一次写入单调时钟；同 phase 间隔 < _THROTTLE_SEC 则跳过
_last: dict[str, float] = {}
_THROTTLE_SEC = 3.0


async def emit_activity(
    project_id: int | None,
    message: str,
    *,
    task_id: int | None = None,
    phase: str = "activity",
    agent: dict | None = None,
    throttle: bool = True,
) -> None:
    """写入 AGENT_ACTIVITY；失败吞掉。throttle 时同 phase 3s 内去重。"""
    if project_id is None and task_id is None:
        return
    key = f"{project_id}:{task_id}:{phase}"
    now = time.monotonic()
    if throttle:
        prev = _last.get(key, 0.0)
        if now - prev < _THROTTLE_SEC:
            return
        _last[key] = now
    try:
        from project_runtime.event import EventEngine, RuntimeEventType

        payload: dict[str, Any] = {"text": message, "phase": phase}
        if agent:
            payload["agent_instance_id"] = agent.get("instance_id")
            if agent.get("name"):
                payload["agent_name"] = agent["name"]
        await EventEngine().record(
            RuntimeEventType.AGENT_ACTIVITY,
            project_id=project_id,
            task_id=task_id,
            phase=phase,
            message=message[:120],
            actor="RUNTIME",
            payload=payload,
        )
    except Exception:  # noqa: BLE001
        pass
