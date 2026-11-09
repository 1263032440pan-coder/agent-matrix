"""办公后端配置面（OFF-13）— 仅配置，不触发项目状态变更。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/office_backend_settings.json")

FallbackMode = Literal["auto", "strict"]


class BackendOverride(BaseModel):
    enabled: bool | None = None
    priority: int | None = None


class LastFallback(BaseModel):
    op: str = ""
    from_backend: str = Field(default="", alias="from")
    to_backend: str = Field(default="", alias="to")
    at: str = ""

    model_config = {"populate_by_name": True}


class OfficeBackendSettings(BaseModel):
    fallback: FallbackMode = "auto"
    backends: dict[str, BackendOverride] = Field(default_factory=dict)
    last_fallback: LastFallback | None = None
    # 用户自备 OfficeCLI 路径（亦可依赖环境变量 OFFICECLI_PATH）
    officecli_path: str = ""


def load_office_backend_settings() -> OfficeBackendSettings:
    if _CONFIG_PATH.exists():
        try:
            return OfficeBackendSettings(
                **json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("office_backend_settings.json unreadable: %s", e)
    return OfficeBackendSettings()


def save_office_backend_settings(cfg: OfficeBackendSettings) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(cfg.model_dump(by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_fallback_mode() -> FallbackMode:
    return load_office_backend_settings().fallback


def record_last_fallback(
    *,
    op: str,
    from_backend: str,
    to_backend: str,
) -> None:
    cfg = load_office_backend_settings()
    cfg.last_fallback = LastFallback.model_validate({
        "op": op,
        "from": from_backend,
        "to": to_backend,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    save_office_backend_settings(cfg)


def apply_office_backend_config(registry: Any) -> None:
    """把已持久化的 enabled/priority 覆写到 registry（幂等）。"""
    from project_runtime.extension.registry import CapabilityType

    cfg = load_office_backend_settings()
    # 可选：把路径写入环境，供 officecli runner 使用
    if cfg.officecli_path:
        import os

        os.environ.setdefault("OFFICECLI_PATH", cfg.officecli_path)

    for info in registry.list_providers(CapabilityType.ARTIFACT_PROCESSOR):
        ov = cfg.backends.get(info.name)
        if ov is None:
            continue
        if ov.enabled is not None:
            registry.set_enabled(info.name, bool(ov.enabled))
        if ov.priority is not None:
            registry.set_priority(info.name, int(ov.priority))


def ensure_office_backends_registered(registry: Any) -> None:
    """API / 组合根：确保内置后端已在 shared registry。"""
    from project_runtime.extension.registry import CapabilityType
    from project_runtime.office.backends import register_office_backends

    existing = registry.list_providers(CapabilityType.ARTIFACT_PROCESSOR)
    if not any(
        i.name.startswith("office-builtin:") for i in existing
    ):
        register_office_backends(registry)
    apply_office_backend_config(registry)
