"""ResourceGovernor — resource-based dispatch decisions (V0.1 Lite).

Decisions (WP-036 subset):
  - ALLOW_DISPATCH: capacity available, proceed.
  - DEFER_RESOURCE: no capacity — task stays READY, never FAILED.

V0.1 Lite scope: concurrency-slot based decisions only (max_workers).
Resource Profiles (LOW/STANDARD/HIGH) and memory-pressure deferral are
post-Demo (see WP-036).
"""

from __future__ import annotations

import enum
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Task

logger = logging.getLogger(__name__)


class ResourceDecision(str, enum.Enum):
    ALLOW_DISPATCH = "ALLOW_DISPATCH"
    DEFER_RESOURCE = "DEFER_RESOURCE"
    RUN_EXCLUSIVE = "RUN_EXCLUSIVE"          # post-Demo
    REDUCE_PARALLELISM = "REDUCE_PARALLELISM"  # post-Demo


class ResourceGovernor:
    """Lite governor: concurrency slots per project.

    Hard rules (WP-036):
      1. Never mutates Task Graph dependencies.
      2. Never changes Automation Mode.
      3. Resource shortage → DEFER_RESOURCE, NEVER task FAIL.
    """

    def __init__(self, session: AsyncSession, max_workers: int = 1):
        self._session = session
        self._max_workers = max_workers

    async def evaluate(self, project_id: int) -> ResourceDecision:
        """Return ALLOW_DISPATCH if a slot is free, else DEFER_RESOURCE."""
        running = await self._running_count(project_id)
        if running < self._max_workers:
            return ResourceDecision.ALLOW_DISPATCH
        logger.info(
            "DEFER_RESOURCE project=%d running=%d max=%d",
            project_id, running, self._max_workers,
        )
        return ResourceDecision.DEFER_RESOURCE

    async def _running_count(self, project_id: int) -> int:
        stmt = select(func.count(Task.id)).where(
            Task.project_id == project_id, Task.status == TaskStatus.RUNNING
        )
        return (await self._session.execute(stmt)).scalar_one()
