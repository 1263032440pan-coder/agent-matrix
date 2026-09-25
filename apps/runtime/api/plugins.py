"""Plugin Management API (WP-032 / 04 规范 Plugin Management API).

    GET    /api/plugins
    POST   /api/plugins/install          (multipart: package zip)
    POST   /api/plugins/{id}/enable
    POST   /api/plugins/{id}/disable
    DELETE /api/plugins/{id}
    GET    /api/plugins/{id}/health
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.plugin import PluginManager, PluginManagerError
from project_runtime.plugin.registry import PluginRegistryError

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# Runtime-wide live manager state: plugin_id → manager instance would leak
# processes across requests; keep a module-level process holder instead.
_ACTIVE_MANAGERS: dict[str, PluginManager] = {}


def _serialize(rec: dict) -> dict:
    import json as _json

    out = dict(rec)
    for k in ("extensions",):
        if isinstance(out.get(k), str):
            try:
                out[k] = _json.loads(out[k])
            except (TypeError, ValueError):
                pass
    return out


@router.get("")
async def list_plugins():
    async with AsyncSession(engine) as session:
        mgr = PluginManager(session)
        plugins = await mgr.list_plugins()
        await session.commit()
    return [_serialize(p) for p in plugins]


@router.post("/install", status_code=201)
async def install_plugin(file: UploadFile):
    """Install a plugin package (ZIP containing plugin.yaml + entrypoint)."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip plugin packages are accepted")

    content = await file.read()
    with tempfile.TemporaryDirectory(prefix="plugin_pkg_") as tmp:
        pkg_dir = Path(tmp) / "pkg"
        pkg_dir.mkdir()
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(content)) as zf:
                # Safety: no path traversal
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    rel = info.filename
                    if ".." in Path(rel).parts or Path(rel).is_absolute():
                        raise HTTPException(422, f"Path traversal rejected: {rel}")
                    zf.extract(info, pkg_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Not a valid ZIP file") from None

        # Handle zip-with-single-root-dir layout
        entries = [p for p in pkg_dir.rglob("plugin.yaml")]
        if not entries:
            raise HTTPException(422, "plugin.yaml not found in package")
        root = entries[0].parent

        async with AsyncSession(engine) as session:
            mgr = PluginManager(session)
            try:
                result = await mgr.install(str(root))
                await session.commit()
                return result
            except PluginManagerError as e:
                await session.rollback()
                raise HTTPException(422, str(e)) from e


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str):
    async with AsyncSession(engine) as session:
        mgr = PluginManager(session)
        try:
            result = await mgr.enable(plugin_id)
            await session.commit()
            return result
        except PluginManagerError as e:
            await session.rollback()
            raise HTTPException(409, str(e)) from e


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    async with AsyncSession(engine) as session:
        mgr = PluginManager(session)
        try:
            result = await mgr.disable(plugin_id)
            await session.commit()
            return result
        except PluginManagerError as e:
            await session.rollback()
            raise HTTPException(409, str(e)) from e


@router.post("/{plugin_id}/start")
async def start_plugin(plugin_id: str):
    """Start the plugin's external process (stdio JSON-RPC)."""
    async with AsyncSession(engine) as session:
        mgr = PluginManager(session)
        try:
            result = await mgr.start(plugin_id)
            await session.commit()
            _ACTIVE_MANAGERS[plugin_id] = mgr
            return result
        except PluginManagerError as e:
            await session.rollback()
            raise HTTPException(500, str(e)) from e


@router.post("/{plugin_id}/stop")
async def stop_plugin(plugin_id: str):
    async with AsyncSession(engine) as session:
        mgr = _ACTIVE_MANAGERS.get(plugin_id) or PluginManager(session)
        try:
            result = await mgr.stop(plugin_id)
            await session.commit()
            _ACTIVE_MANAGERS.pop(plugin_id, None)
            return result
        except PluginManagerError as e:
            await session.rollback()
            raise HTTPException(409, str(e)) from e


@router.get("/{plugin_id}/health")
async def plugin_health(plugin_id: str):
    async with AsyncSession(engine) as session:
        mgr = _ACTIVE_MANAGERS.get(plugin_id) or PluginManager(session)
        try:
            result = await mgr.health(plugin_id)
            await session.commit()
            return result
        except PluginManagerError as e:
            await session.rollback()
            raise HTTPException(404, str(e)) from e


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str):
    async with AsyncSession(engine) as session:
        mgr = _ACTIVE_MANAGERS.get(plugin_id) or PluginManager(session)
        try:
            result = await mgr.uninstall(plugin_id)
            await session.commit()
            _ACTIVE_MANAGERS.pop(plugin_id, None)
            return result
        except (PluginManagerError, PluginRegistryError) as e:
            await session.rollback()
            raise HTTPException(409, str(e)) from e
