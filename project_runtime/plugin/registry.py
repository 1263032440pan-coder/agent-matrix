"""Plugin Registry — SQLite persistence for plugin lifecycle state (WP-032).

Lifecycle state machine (spec):
    DISCOVERED → INSTALLED → (enable) STARTING → RUNNING ⇄ DEGRADED
                                    ↓ (disable/stop)
                                 DISABLED / STOPPED
    any → FAILED → (uninstall) UNINSTALLED

Security boundary: the registry stores metadata ONLY — plugins never get DB
connections or Project State write access through this registry.
"""

from __future__ import annotations

import datetime
import enum
import json

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class PluginStatus(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    INSTALLED = "INSTALLED"
    DISABLED = "DISABLED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    UNINSTALLED = "UNINSTALLED"


# Valid transitions (lifecycle enforcement at registry level)
TRANSITIONS: dict[PluginStatus, set[PluginStatus]] = {
    PluginStatus.DISCOVERED: {PluginStatus.INSTALLED, PluginStatus.UNINSTALLED},
    PluginStatus.INSTALLED: {
        PluginStatus.DISABLED, PluginStatus.STARTING, PluginStatus.UNINSTALLED,
    },
    PluginStatus.DISABLED: {
        PluginStatus.INSTALLED, PluginStatus.STARTING, PluginStatus.UNINSTALLED,
    },
    PluginStatus.STARTING: {
        PluginStatus.RUNNING, PluginStatus.FAILED, PluginStatus.STOPPED,
    },
    PluginStatus.RUNNING: {
        PluginStatus.DEGRADED, PluginStatus.STOPPED, PluginStatus.FAILED,
        PluginStatus.DISABLED,
    },
    PluginStatus.DEGRADED: {
        PluginStatus.RUNNING, PluginStatus.STOPPED, PluginStatus.FAILED,
    },
    PluginStatus.FAILED: {PluginStatus.STOPPED, PluginStatus.STARTING},
    PluginStatus.STOPPED: {PluginStatus.STARTING, PluginStatus.UNINSTALLED},
    PluginStatus.UNINSTALLED: set(),
}


class PluginRegistryError(Exception):
    pass


class InvalidPluginTransitionError(PluginRegistryError):
    pass


class PluginRecord(Base):
    __tablename__ = "plugin"

    plugin_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PluginStatus.INSTALLED.value
    )
    enabled: Mapped[str] = mapped_column(
        String(8), nullable=False, default="false"
    )  # "true"/"false" — user intent, independent of runtime state
    permissions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extensions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    package_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    health: Mapped[str | None] = mapped_column(String(16), nullable=True)

    installed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class PluginRegistry:
    """SQLite-backed registry. Owns state transitions only — never processes."""

    def __init__(self, session):
        self._session = session

    async def install(
        self,
        *,
        plugin_id: str,
        version: str,
        name: str,
        api_version: str,
        description: str | None,
        permissions: list[str],
        extensions: list[dict],
        runtime: dict,
        package_path: str,
    ) -> PluginRecord:
        existing = await self.get(plugin_id)
        if existing is not None:
            raise PluginRegistryError(
                f"Plugin already installed: {plugin_id}@{existing.version}"
            )
        rec = PluginRecord(
            plugin_id=plugin_id,
            version=version,
            name=name,
            api_version=api_version,
            description=description,
            status=PluginStatus.INSTALLED.value,
            enabled="false",
            permissions_json=json.dumps(permissions, ensure_ascii=False),
            extensions_json=json.dumps(extensions, ensure_ascii=False),
            runtime_json=json.dumps(runtime, ensure_ascii=False),
            package_path=package_path,
        )
        self._session.add(rec)
        await self._session.flush()
        return rec

    async def get(self, plugin_id: str) -> PluginRecord | None:
        from sqlalchemy import select

        stmt = select(PluginRecord).where(PluginRecord.plugin_id == plugin_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[PluginRecord]:
        from sqlalchemy import select

        stmt = select(PluginRecord).order_by(PluginRecord.installed_at)
        return list((await self._session.execute(stmt)).scalars().all())

    async def transition(
        self, plugin_id: str, to: PluginStatus, *, force: bool = False
    ) -> PluginRecord:
        """Lifecycle transition with state-machine enforcement."""
        rec = await self.get(plugin_id)
        if rec is None:
            raise PluginRegistryError(f"Plugin not found: {plugin_id}")
        current = PluginStatus(rec.status)
        target = to
        if target not in TRANSITIONS[current] and not force:
            raise InvalidPluginTransitionError(
                f"Illegal plugin transition {current.value} → {target.value} "
                f"for {plugin_id}"
            )
        rec.status = target.value
        await self._session.flush()
        return rec

    async def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        rec = await self.get(plugin_id)
        if rec is None:
            raise PluginRegistryError(f"Plugin not found: {plugin_id}")
        rec.enabled = "true" if enabled else "false"
        await self._session.flush()
        return rec

    async def set_health(self, plugin_id: str, health: str | None) -> PluginRecord:
        rec = await self.get(plugin_id)
        if rec is None:
            raise PluginRegistryError(f"Plugin not found: {plugin_id}")
        rec.health = health
        await self._session.flush()
        return rec

    async def uninstall(self, plugin_id: str) -> None:
        rec = await self.get(plugin_id)
        if rec is None:
            raise PluginRegistryError(f"Plugin not found: {plugin_id}")
        if PluginStatus(rec.status) not in (
            PluginStatus.STOPPED, PluginStatus.DISCOVERED,
            PluginStatus.INSTALLED, PluginStatus.FAILED,
        ):
            raise PluginRegistryError(
                f"Cannot uninstall {plugin_id} in state {rec.status} — stop it first"
            )
        await self.transition(plugin_id, PluginStatus.UNINSTALLED, force=True)
        await self._session.delete(rec)
        await self._session.flush()
