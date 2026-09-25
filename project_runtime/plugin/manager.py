"""Plugin Manager (WP-032) — lifecycle orchestration for external plugins.

Red lines (spec 安全边界):
  - Plugins run as EXTERNAL PROCESSES (stdio JSON-RPC) — never in-process.
  - Plugins never receive DB connections or Project State write access.
  - A plugin crash/exit NEVER takes down the Project Runtime (acceptance §5).

Operations (WP-032 必须实现):
    install / validate_manifest / enable / disable / start / stop / health / uninstall
"""


from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project_runtime.plugin.bridge import PluginBridge

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.plugin.manifest import (
    RUNTIME_PLUGIN_API,
    ManifestError,
    api_compatible,
    load_and_validate_manifest,
)
from project_runtime.plugin.registry import (
    PluginRegistry,
    PluginRegistryError,
    PluginStatus,
)
from project_runtime.plugin.rpc import PluginRPCClient, PluginRPCError

logger = logging.getLogger(__name__)

# Plugins live on disk here (installed copies)
PLUGIN_STORE = Path("data/plugins")


class PluginManagerError(Exception):
    pass


class PluginManager:
    def __init__(self, session: AsyncSession, bridge: "PluginBridge | None" = None):
        self._session = session
        self._registry = PluginRegistry(session)
        # plugin_id → live RPC client (runtime state, not persisted)
        self._processes: dict[str, PluginRPCClient] = {}
        # WP-033 bridge: capability providers in the unified D16 registry
        self._bridge = bridge

    # ── install / validate ─────────────────────────────────────────────

    async def install(self, package_dir: str | Path) -> dict:
        """Validate manifest → copy package into store → register INSTALLED.

        API incompatibility is RECORDED (enable will reject) per acceptance §3.
        """
        # 1. Manifest validation (hard gate)
        try:
            manifest = load_and_validate_manifest(package_dir)
        except ManifestError as e:
            raise PluginManagerError(f"Install rejected: {e}") from e

        # 2. Copy package into persistent store
        PLUGIN_STORE.mkdir(parents=True, exist_ok=True)
        dest = PLUGIN_STORE / manifest.plugin_id
        if dest.exists():
            raise PluginManagerError(
                f"Plugin directory already exists: {dest}"
            )
        shutil.copytree(str(package_dir), str(dest))

        # 3. Register (metadata only — no DB handle crosses this boundary)
        rec = await self._registry.install(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            name=manifest.name,
            api_version=manifest.api_version,
            description=manifest.description,
            permissions=manifest.permissions,
            extensions=[e.model_dump(mode="json") for e in manifest.extensions],
            runtime=manifest.runtime.model_dump(mode="json"),
            package_path=str(dest),
        )
        await self._session.flush()

        api_ok = api_compatible(manifest.api_version)
        if not api_ok:
            logger.warning(
                "Plugin %s api_version %s incompatible with runtime %s — "
                "enable will be rejected",
                manifest.plugin_id, manifest.api_version, RUNTIME_PLUGIN_API,
            )
        return {
            "plugin_id": rec.plugin_id,
            "version": rec.version,
            "status": rec.status,
            "api_compatible": api_ok,
        }

    # ── enable / disable ───────────────────────────────────────────────

    async def enable(self, plugin_id: str) -> dict:
        rec = await self._registry.get(plugin_id)
        if rec is None:
            raise PluginManagerError(f"Plugin not found: {plugin_id}")
        # Acceptance §3: incompatible API version → reject enable
        if not api_compatible(rec.api_version):
            raise PluginManagerError(
                f"Enable rejected: plugin api_version {rec.api_version} "
                f"incompatible with runtime {RUNTIME_PLUGIN_API}"
            )
        await self._registry.set_enabled(plugin_id, True)
        await self._session.flush()
        return {"plugin_id": plugin_id, "enabled": True}

    async def disable(self, plugin_id: str) -> dict:
        # Disabling also stops a running process
        await self.stop(plugin_id)
        await self._registry.set_enabled(plugin_id, False)
        await self._session.flush()
        return {"plugin_id": plugin_id, "enabled": False}

    # ── start / stop (process lifecycle) ───────────────────────────────

    async def start(self, plugin_id: str) -> dict:
        rec = await self._registry.get(plugin_id)
        if rec is None:
            raise PluginManagerError(f"Plugin not found: {plugin_id}")
        if rec.enabled != "true":
            raise PluginManagerError(f"Plugin {plugin_id} is not enabled")
        if plugin_id in self._processes:
            return {"plugin_id": plugin_id, "status": "RUNNING"}

        manifest = load_and_validate_manifest(rec.package_path)
        entry = Path(rec.package_path) / manifest.entrypoint
        await self._registry.transition(plugin_id, PluginStatus.STARTING)

        try:
            client = await PluginRPCClient.spawn(
                ["python", str(entry)],  # launcher command
                cwd=rec.package_path,
            )
            self._processes[plugin_id] = client
            # Handshake: plugin.initialize
            result = await client.initialize(RUNTIME_PLUGIN_API)
            await self._registry.transition(plugin_id, PluginStatus.RUNNING)
            await self._registry.set_health(plugin_id, "healthy")
            await self._session.flush()
            logger.info("Plugin %s RUNNING (init: %s)", plugin_id, result)

            # WP-033: bridge capability extensions into the unified registry
            bridged: list[str] = []
            if self._bridge is not None:
                import json as _json

                exts = _json.loads(rec.extensions_json or "[]")
                perms = _json.loads(rec.permissions_json or "[]")
                bridged = self._bridge.register_plugin(plugin_id, exts, perms, client)

            return {
                "plugin_id": plugin_id, "status": "RUNNING",
                "bridged_providers": bridged,
            }
        except (PluginRPCError, ManifestError, OSError) as e:
            # Failure lands in FAILED — Runtime itself keeps running (§5)
            logger.error("Plugin %s failed to start: %s", plugin_id, e)
            await self._registry.transition(plugin_id, PluginStatus.FAILED, force=True)
            await self._registry.set_health(plugin_id, f"error: {str(e)[:100]}")
            await self._session.flush()
            self._processes.pop(plugin_id, None)
            raise PluginManagerError(f"Start failed: {e}") from e

    async def stop(self, plugin_id: str) -> dict:
        rec = await self._registry.get(plugin_id)
        if rec is None:
            raise PluginManagerError(f"Plugin not found: {plugin_id}")
        # WP-033: detach capability providers first
        if self._bridge is not None:
            self._bridge.unregister_plugin(plugin_id)
        client = self._processes.pop(plugin_id, None)
        if client is not None:
            # Graceful shutdown request, then hard kill
            try:
                await client.shutdown()
            except PluginRPCError:
                pass
            await client.terminate()
        current = PluginStatus(rec.status)
        if current in (PluginStatus.RUNNING, PluginStatus.STARTING, PluginStatus.DEGRADED):
            await self._registry.transition(plugin_id, PluginStatus.STOPPED)
        elif current == PluginStatus.FAILED:
            await self._registry.transition(plugin_id, PluginStatus.STOPPED, force=True)
        await self._session.flush()
        return {"plugin_id": plugin_id, "status": "STOPPED"}

    # ── health / uninstall ─────────────────────────────────────────────

    async def health(self, plugin_id: str) -> dict:
        rec = await self._registry.get(plugin_id)
        if rec is None:
            raise PluginManagerError(f"Plugin not found: {plugin_id}")
        client = self._processes.get(plugin_id)
        if client is None:
            return {"plugin_id": plugin_id, "status": rec.status, "live": False}
        try:
            result = await client.health()
            await self._registry.set_health(plugin_id, "healthy")
            await self._session.flush()
            return {"plugin_id": plugin_id, "status": "RUNNING", "live": True, "detail": result}
        except PluginRPCError as e:
            # Process dead/unresponsive — Runtime stays up; mark DEGRADED
            await self._registry.transition(
                plugin_id, PluginStatus.DEGRADED, force=True
            )
            await self._registry.set_health(plugin_id, f"degraded: {str(e)[:100]}")
            await self._session.flush()
            self._processes.pop(plugin_id, None)
            return {"plugin_id": plugin_id, "status": "DEGRADED", "live": False}

    async def uninstall(self, plugin_id: str) -> dict:
        await self.stop(plugin_id)  # must be stopped (registry enforces)
        await self._registry.uninstall(plugin_id)
        await self._session.flush()
        # Remove package from store (best-effort)
        dest = PLUGIN_STORE / plugin_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        return {"plugin_id": plugin_id, "uninstalled": True}

    # ── list ────────────────────────────────────────────────────────────

    async def list_plugins(self) -> list[dict]:
        recs = await self._registry.list_all()
        return [
            {
                "plugin_id": r.plugin_id,
                "name": r.name,
                "version": r.version,
                "api_version": r.api_version,
                "status": r.status,
                "enabled": r.enabled == "true",
                "health": r.health,
                "extensions": r.extensions_json,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
            }
            for r in recs
        ]


# Re-exports for API layer
__all__ = [
    "PluginManager",
    "PluginManagerError",
    "PluginRegistryError",
    "PluginStatus",
]
