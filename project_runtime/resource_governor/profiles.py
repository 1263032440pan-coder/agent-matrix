"""Resource Profiles + system snapshot (WP-036 / ADR-026).

Profile 档位 (LOW/STANDARD/HIGH/CUSTOM) 决定实际并发边界。
Profile 与 Automation Mode 正交（核心规则 2）——LOW 不改变 FULL_AUTO 语义。
"""

from __future__ import annotations

import dataclasses
import enum


class ResourceProfile(str, enum.Enum):
    LOW = "LOW"
    STANDARD = "STANDARD"
    HIGH = "HIGH"
    CUSTOM = "CUSTOM"


@dataclasses.dataclass(frozen=True)
class ProfileSettings:
    """Concrete resource bounds per profile (WP-036 输出 ResourceProfile Settings)."""

    worker_concurrency: int      # max parallel RUNNING tasks
    browser_contexts: int        # shared browser pool limit (rule 6)
    plugin_max_parallel: int     # optional plugin processes
    heavy_task_exclusive: bool   # rule 5: heavy tasks run alone
    memory_pressure_mb: int      # free-RAM threshold for DEFER_RESOURCE


# ADR-026: 8GB floor → LOW defaults (concurrency 1, single browser context)
PROFILE_SETTINGS: dict[ResourceProfile, ProfileSettings] = {
    ResourceProfile.LOW: ProfileSettings(
        worker_concurrency=1,
        browser_contexts=1,
        plugin_max_parallel=1,
        heavy_task_exclusive=True,
        memory_pressure_mb=1024,  # below 1GB free → defer
    ),
    ResourceProfile.STANDARD: ProfileSettings(
        worker_concurrency=3,
        browser_contexts=2,
        plugin_max_parallel=2,
        heavy_task_exclusive=False,
        memory_pressure_mb=512,
    ),
    ResourceProfile.HIGH: ProfileSettings(
        worker_concurrency=8,
        browser_contexts=4,
        plugin_max_parallel=4,
        heavy_task_exclusive=False,
        memory_pressure_mb=256,
    ),
}


def settings_for(profile: ResourceProfile) -> ProfileSettings:
    if profile is ResourceProfile.CUSTOM:
        # CUSTOM callers override via ResourceGovernor(..., custom_settings=...)
        # — fall back to STANDARD bounds unless overridden.
        return PROFILE_SETTINGS[ResourceProfile.STANDARD]
    return PROFILE_SETTINGS[profile]


@dataclasses.dataclass
class SystemResourceSnapshot:
    """Live system readings (WP-036 输出). Collected via psutil when
    available; test environments may inject values directly."""

    total_memory_mb: int
    available_memory_mb: int
    cpu_percent: float

    @property
    def memory_pressure(self) -> bool:
        """Pressure == available below the LOW threshold — conservative
        global floor regardless of profile (profiles tighten further)."""
        return self.available_memory_mb < PROFILE_SETTINGS[
            ResourceProfile.LOW
        ].memory_pressure_mb


def snapshot() -> SystemResourceSnapshot:
    """Collect a real snapshot (psutil); falls back to generous defaults
    when psutil is unavailable (never blocks dispatch on telemetry)."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        return SystemResourceSnapshot(
            total_memory_mb=int(mem.total / (1024 * 1024)),
            available_memory_mb=int(mem.available / (1024 * 1024)),
            cpu_percent=psutil.cpu_percent(interval=0.1),
        )
    except ImportError:
        return SystemResourceSnapshot(
            total_memory_mb=8192, available_memory_mb=4096, cpu_percent=0.0
        )


def auto_profile(snap: SystemResourceSnapshot) -> ResourceProfile:
    """ADR-026 mapping: ≤8GB → LOW (the product's minimum spec), ≤16GB →
    STANDARD, above → HIGH. CUSTOM is always explicit, never auto."""
    if snap.total_memory_mb <= 8192:
        return ResourceProfile.LOW
    if snap.total_memory_mb <= 16384:
        return ResourceProfile.STANDARD
    return ResourceProfile.HIGH
