"""办公 ARTIFACT_PROCESSOR 后端配置 API（OFF-13 / 方案 §六）。

仅配置面：列表 / 启停 / 优先级 / auto·strict；不触发项目状态变更。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from project_runtime.extension.registry import CapabilityType, ExtensionError
from project_runtime.extension.shared import shared_registry
from project_runtime.office.config import (
    BackendOverride,
    apply_office_backend_config,
    ensure_office_backends_registered,
    load_office_backend_settings,
    save_office_backend_settings,
)
from project_runtime.worker.tool_surface import provider_is_usable

router = APIRouter(prefix="/api/settings/office-backends", tags=["office-backends"])


def _source_of(name: str) -> str:
    if name.startswith("office-cli"):
        return "plugin"
    if name.startswith("office-builtin") or name.startswith("office-soffice"):
        return "builtin"
    return "other"


def _op_of(name: str) -> str:
    return name.split(":", 1)[-1]


async def _serialize_one(info) -> dict:
    prov = info.provider
    healthy = False
    version = ""
    if prov is not None and info.enabled:
        try:
            healthy = bool(await prov.health())
        except Exception:  # noqa: BLE001
            healthy = False
        ver_fn = getattr(prov, "version_label", None)
        if callable(ver_fn):
            try:
                version = str(ver_fn() or "")
            except Exception:  # noqa: BLE001
                version = ""
    usable = False
    if prov is not None:
        try:
            usable = provider_is_usable(prov, shared_registry())
        except Exception:  # noqa: BLE001
            usable = False
    return {
        "name": info.name,
        "op": _op_of(info.name),
        "source": _source_of(info.name),
        "priority": info.priority,
        "enabled": info.enabled,
        "usable": usable,
        "health": healthy,
        "version": version,
    }


@router.get("")
async def list_office_backends():
    registry = shared_registry()
    ensure_office_backends_registered(registry)
    apply_office_backend_config(registry)
    cfg = load_office_backend_settings()
    backends = []
    for info in registry.list_providers(CapabilityType.ARTIFACT_PROCESSOR):
        backends.append(await _serialize_one(info))
    return {
        "fallback": cfg.fallback,
        "officecli_path": cfg.officecli_path,
        "last_fallback": (
            cfg.last_fallback.model_dump(by_alias=True)
            if cfg.last_fallback
            else None
        ),
        "backends": backends,
    }


class GlobalPatch(BaseModel):
    fallback: str | None = None
    officecli_path: str | None = None


@router.patch("")
async def patch_office_backend_global(body: GlobalPatch):
    cfg = load_office_backend_settings()
    if body.fallback is not None:
        if body.fallback not in ("auto", "strict"):
            raise HTTPException(400, "fallback 仅允许 auto 或 strict")
        cfg.fallback = body.fallback  # type: ignore[assignment]
    if body.officecli_path is not None:
        cfg.officecli_path = body.officecli_path
        import os

        if body.officecli_path.strip():
            os.environ["OFFICECLI_PATH"] = body.officecli_path.strip()
        else:
            os.environ.pop("OFFICECLI_PATH", None)
    save_office_backend_settings(cfg)
    return await list_office_backends()


class BackendPatch(BaseModel):
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


@router.patch("/{backend_name:path}")
async def patch_office_backend(backend_name: str, body: BackendPatch):
    registry = shared_registry()
    ensure_office_backends_registered(registry)
    info = registry.get(backend_name)
    if info is None or info.capability != CapabilityType.ARTIFACT_PROCESSOR:
        raise HTTPException(404, f"后端未注册: {backend_name}")
    cfg = load_office_backend_settings()
    ov = cfg.backends.get(backend_name) or BackendOverride()
    try:
        if body.enabled is not None:
            registry.set_enabled(backend_name, body.enabled)
            ov.enabled = body.enabled
        if body.priority is not None:
            registry.set_priority(backend_name, body.priority)
            ov.priority = body.priority
    except ExtensionError as e:
        raise HTTPException(409, str(e)) from e
    cfg.backends[backend_name] = ov
    save_office_backend_settings(cfg)
    return await list_office_backends()
