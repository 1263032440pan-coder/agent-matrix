"""OFF-13：办公后端配置 API + 配置持久化。"""

from __future__ import annotations

import pytest

from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.office.backends import register_office_backends
from project_runtime.office.config import (
    BackendOverride,
    OfficeBackendSettings,
    apply_office_backend_config,
    load_office_backend_settings,
    save_office_backend_settings,
)


@pytest.fixture()
def office_cfg(tmp_path, monkeypatch):
    path = tmp_path / "office_backend_settings.json"
    monkeypatch.setattr(
        "project_runtime.office.config._CONFIG_PATH", path,
    )
    return path


@pytest.fixture()
def isolated_registry(monkeypatch):
    from project_runtime.extension import shared as shared_mod

    reg = ExtensionRegistry()
    monkeypatch.setattr(shared_mod, "_registry", reg)
    return reg


def test_persist_fallback_and_override(office_cfg):
    cfg = OfficeBackendSettings(
        fallback="strict",
        backends={
            "office-cli:office.render.pptx": BackendOverride(
                enabled=False, priority=50,
            ),
        },
    )
    save_office_backend_settings(cfg)
    loaded = load_office_backend_settings()
    assert loaded.fallback == "strict"
    assert loaded.backends["office-cli:office.render.pptx"].enabled is False
    assert loaded.backends["office-cli:office.render.pptx"].priority == 50


def test_apply_config_to_registry(office_cfg):
    reg = ExtensionRegistry()
    register_office_backends(reg)
    name = "office-builtin:office.render.pptx"
    save_office_backend_settings(
        OfficeBackendSettings(
            backends={name: BackendOverride(enabled=False, priority=10)},
        )
    )
    apply_office_backend_config(reg)
    info = reg.get(name)
    assert info is not None
    assert info.enabled is False
    assert info.priority == 10


@pytest.mark.asyncio
async def test_api_list_and_patch(office_cfg, isolated_registry):
    from fastapi import HTTPException

    from apps.runtime.api.office_backends import (
        BackendPatch,
        GlobalPatch,
        list_office_backends,
        patch_office_backend,
        patch_office_backend_global,
    )

    body = await list_office_backends()
    assert body["fallback"] == "auto"
    assert any(
        b["name"].startswith("office-builtin:") for b in body["backends"]
    )

    body2 = await patch_office_backend_global(GlobalPatch(fallback="strict"))
    assert body2["fallback"] == "strict"

    target = "office-builtin:office.render.pptx"
    body3 = await patch_office_backend(
        target, BackendPatch(enabled=False, priority=42),
    )
    row = next(b for b in body3["backends"] if b["name"] == target)
    assert row["enabled"] is False
    assert row["priority"] == 42

    with pytest.raises(HTTPException) as ei:
        await patch_office_backend_global(GlobalPatch(fallback="maybe"))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei2:
        await patch_office_backend(
            "no-such:backend", BackendPatch(enabled=True),
        )
    assert ei2.value.status_code == 404
