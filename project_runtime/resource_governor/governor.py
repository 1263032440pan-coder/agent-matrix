"""ResourceGovernor — profile-driven resource decisions (WP-036).

D15 Lite upgraded IN PLACE (包装整合，非替换): the concurrency-slot core is
kept; Profiles / memory pressure / exclusive decisions layer on top. The
Scheduler call site (governor.evaluate) is unchanged — decisions stay
advisory-only (Scheduler already handles DEFER_RESOURCE correctly).

Decisions:
  - ALLOW_DISPATCH        capacity available
  - DEFER_RESOURCE        no slot OR memory pressure (task stays READY —
                          NEVER FAILED, core rule 3)
  - RUN_EXCLUSIVE         heavy task + heavy_task_exclusive → run alone
                          (other slots withheld while it runs)
  - REDUCE_PARALLELISM    slots shrink mid-flight (memory tightening) —
                          in-flight tasks keep running; NEW dispatch defers

Hard rules (unchanged from D15):
  1. Never mutates Task Graph dependencies.
  2. Never changes Automation Mode.
  3. Resource shortage → DEFER_RESOURCE, NEVER task FAIL.
"""

from __future__ import annotations

import enum
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Task
from project_runtime.resource_governor.profiles import (
    ProfileSettings,
    ResourceProfile,
    SystemResourceSnapshot,
    settings_for,
)

logger = logging.getLogger(__name__)


class ResourceDecision(str, enum.Enum):
    ALLOW_DISPATCH = "ALLOW_DISPATCH"
    DEFER_RESOURCE = "DEFER_RESOURCE"
    RUN_EXCLUSIVE = "RUN_EXCLUSIVE"
    REDUCE_PARALLELISM = "REDUCE_PARALLELISM"


class ResourceGovernor:
    """Profile-driven governor (D15 concurrency core + WP-036 layers)."""

    def __init__(
        self,
        session: AsyncSession,
        max_workers: int = 1,
        *,
        profile: ResourceProfile | None = None,
        custom_settings: ProfileSettings | None = None,
        snapshot_provider=None,
    ):
        self._session = session
        # Backward compat: D15 callers pass max_workers directly (no
        # profile) — that keeps working as an implicit CUSTOM profile.
        self._profile = profile or ResourceProfile.CUSTOM
        self._settings = custom_settings or _derive_settings(
            max_workers, self._profile
        )
        # Injectable for tests; None → memory checks skipped (D15 compat)
        self._snapshot_provider = snapshot_provider
        self._exclusive_running = False

    @property
    def profile(self) -> ResourceProfile:
        return self._profile

    @property
    def settings(self) -> ProfileSettings:
        return self._settings

    async def evaluate(
        self, project_id: int, *, heavy: bool = False
    ) -> ResourceDecision:
        """Decide dispatch for the next task in this project.

        `heavy` marks resource-heavy work (browser automation, local LLM…).
        """
        # 1. Memory pressure (profile threshold) — DEFER before anything.
        snap = self._take_snapshot()
        if snap is not None and snap.available_memory_mb < self._settings.memory_pressure_mb:
            logger.info(
                "DEFER_RESOURCE (memory pressure %dMB free < %dMB)",
                snap.available_memory_mb,
                self._settings.memory_pressure_mb,
            )
            return ResourceDecision.DEFER_RESOURCE

        # 2. Exclusive mode: while a heavy task runs, everything else defers.
        if self._exclusive_running:
            # WP-036 idle-recycle: if nothing is RUNNING any more, the heavy
            # task finished (or crashed) — exclusive flag self-heals.
            if await self._running_count(project_id) == 0:
                self._exclusive_running = False
            else:
                return ResourceDecision.DEFER_RESOURCE
        if heavy and self._settings.heavy_task_exclusive:
            self._exclusive_running = True
            return ResourceDecision.RUN_EXCLUSIVE

        # 3. Tightening: memory between 1x and 2x threshold → shrink cap.
        effective_max = self._settings.worker_concurrency
        if snap is not None and _tightening(snap, self._settings):
            effective_max = max(1, effective_max - 1)
            if self._settings.worker_concurrency > 1:
                logger.info(
                    "REDUCE_PARALLELISM effective_max=%d (avail %dMB)",
                    effective_max, snap.available_memory_mb,
                )

        running = await self._running_count(project_id)
        if running < effective_max:
            return ResourceDecision.ALLOW_DISPATCH

        # 4. No slot → DEFER (task stays READY, never FAILED — rule 3)
        logger.info(
            "DEFER_RESOURCE project=%d running=%d max=%d",
            project_id, running, effective_max,
        )
        return ResourceDecision.DEFER_RESOURCE

    def release_exclusive(self) -> None:
        """Heavy task finished → exclusive mode off (Scheduler calls this
        after a RUN_EXCLUSIVE task completes)."""
        self._exclusive_running = False

    async def _running_count(self, project_id: int) -> int:
        stmt = select(func.count(Task.id)).where(
            Task.project_id == project_id, Task.status == TaskStatus.RUNNING
        )
        return (await self._session.execute(stmt)).scalar_one()

    def _take_snapshot(self) -> SystemResourceSnapshot | None:
        if self._snapshot_provider is not None:
            try:
                return self._snapshot_provider()
            except Exception:  # noqa: BLE001 — telemetry must not break
                return None
        return None  # no provider → skip memory checks (D15 compat mode)


def _derive_settings(max_workers: int, profile: ResourceProfile) -> ProfileSettings:
    if profile is ResourceProfile.CUSTOM:
        return ProfileSettings(
            worker_concurrency=max_workers,
            browser_contexts=max(1, max_workers),
            plugin_max_parallel=max(1, max_workers),
            heavy_task_exclusive=False,
            memory_pressure_mb=256,
        )
    s = settings_for(profile)
    # explicit max_workers (≠ default) wins over profile — D15 callers' intent
    if max_workers != 1:
        return ProfileSettings(
            worker_concurrency=max_workers,
            browser_contexts=s.browser_contexts,
            plugin_max_parallel=s.plugin_max_parallel,
            heavy_task_exclusive=s.heavy_task_exclusive,
            memory_pressure_mb=s.memory_pressure_mb,
        )
    return s


def _tightening(snap: SystemResourceSnapshot, s: ProfileSettings) -> bool:
    """Memory below 2x the pressure threshold → tighten before deferring."""
    return snap.available_memory_mb < 2 * s.memory_pressure_mb
