"""O4 门面选路：priority / auto 回落 / strict（V1、V3、V4）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry
from project_runtime.office.backends import (
    BuiltinPptxBackend,
    invoke_office_backend,
    register_office_backends,
)
from project_runtime.office.providers import OfficePptxProvider, register_office_tools


class _FailCliPptx:
    name = "office-cli:office.render.pptx"
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    priority = 200
    enabled = True

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        return {"ok": False, "error_code": "RENDER_FAILED", "error": "cli boom"}


class _OkCliPptx:
    name = "office-cli:office.render.pptx"
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    priority = 200
    enabled = True

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        # 写出最小 pptx 走内置，标记 backend
        from project_runtime.office.pptx_render import render_pptx_from_markdown

        out = Path(payload["out_path"])
        meta = render_pptx_from_markdown("# T\n## S\n- a", out)
        return {"ok": True, "meta": meta, "backend": self.name}


@pytest.mark.asyncio
async def test_v1_builtin_only(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_backends(reg)
    out = tmp_path / "a.pptx"
    r = await invoke_office_backend(
        reg,
        "office.render.pptx",
        {"out_path": str(out), "markdown": "# T\n## S\n- a"},
    )
    assert r["ok"] is True
    assert str(r["backend"]).startswith("office-builtin")
    assert out.is_file()


@pytest.mark.asyncio
async def test_v3_fallback_to_builtin_when_cli_fails(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_backends(reg)
    reg.register(_FailCliPptx())
    out = tmp_path / "b.pptx"
    r = await invoke_office_backend(
        reg,
        "office.render.pptx",
        {"out_path": str(out), "markdown": "# T\n## S\n- a"},
        fallback="auto",
    )
    assert r["ok"] is True
    assert str(r["backend"]).startswith("office-builtin")
    assert r.get("fallback_from") == "office-cli:office.render.pptx"
    assert out.is_file()


@pytest.mark.asyncio
async def test_v4_strict_no_fallback(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_backends(reg)
    reg.register(_FailCliPptx())
    out = tmp_path / "c.pptx"
    r = await invoke_office_backend(
        reg,
        "office.render.pptx",
        {"out_path": str(out), "markdown": "# T\n## S\n- a"},
        fallback="strict",
    )
    assert r["ok"] is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_facade_prefers_cli(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_tools(reg)
    # 覆盖同名：先注销再注册成功 CLI
    try:
        reg.unregister("office-cli:office.render.pptx")
    except Exception:  # noqa: BLE001
        pass
    reg.register(_OkCliPptx())
    r = await OfficePptxProvider().invoke({
        "path": "deck.pptx",
        "markdown": "# T\n## S\n- a",
        "__ws_root": str(tmp_path),
        "__registry": reg,
    })
    assert r["ok"] is True
    assert r["backend"] == "office-cli"


@pytest.mark.asyncio
async def test_disable_cli_falls_back(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_backends(reg)
    reg.register(_OkCliPptx())
    reg.set_enabled("office-cli:office.render.pptx", False)
    out = tmp_path / "d.pptx"
    r = await invoke_office_backend(
        reg,
        "office.render.pptx",
        {"out_path": str(out), "markdown": "# T\n## S\n- a"},
    )
    assert r["ok"] is True
    assert str(r["backend"]).startswith("office-builtin")


def test_builtin_backend_priority_lower_than_cli():
    assert BuiltinPptxBackend().priority == 100
    assert _OkCliPptx().priority == 200
