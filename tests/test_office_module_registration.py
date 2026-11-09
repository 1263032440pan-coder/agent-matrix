"""办公模块独立注册/注销（方案 O1 / OFF-01）。"""

from __future__ import annotations

from project_runtime.extension.registry import ExtensionRegistry


def test_unregister_office_tools_removes_from_surface():
    from project_runtime.office.providers import (
        register_office_tools,
        unregister_office_tools,
    )
    from project_runtime.worker.tool_surface import build_tool_surface

    reg = ExtensionRegistry()
    register_office_tools(reg)
    before = build_tool_surface(reg, whitelist=None)
    names = {t["function"]["name"] for t in (before["tools"] or [])}
    assert "tool:office_pptx" in names

    removed = unregister_office_tools(reg)
    assert "tool:office_pptx" in removed
    after = build_tool_surface(reg, whitelist=None)
    names2 = {t["function"]["name"] for t in (after["tools"] or [])}
    assert "tool:office_pptx" not in names2
    assert "tool:office_docx" not in names2


def test_plugin_gap_respects_available_after_unregister():
    from project_runtime.office.providers import (
        register_office_tools,
        unregister_office_tools,
    )
    from project_runtime.worker.obligation_plugins import (
        available_tool_plugins,
        plugin_gap,
    )

    reg = ExtensionRegistry()
    register_office_tools(reg)
    avail = available_tool_plugins(reg)
    assert "office_pptx" in avail
    assert plugin_gap({"office_pptx"}, ["office_pptx"], available_plugins=avail) == set()

    unregister_office_tools(reg)
    avail2 = available_tool_plugins(reg)
    assert "office_pptx" not in avail2
    gap = plugin_gap(
        {"office_pptx"}, ["office_pptx"], available_plugins=avail2,
    )
    assert gap == {"office_pptx"}


def test_office_pptx_returns_backend_builtin():
    """V1：内置后端标记。"""
    import asyncio
    from pathlib import Path

    from project_runtime.office.providers import OfficePptxProvider

    async def _run(tmp: Path):
        p = OfficePptxProvider()
        return await p.invoke({
            "path": "deck.pptx",
            "markdown": "# T\n## S\n- a",
            "__ws_root": str(tmp),
        })

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = asyncio.run(_run(Path(d)))
    assert out.get("ok") is True
    assert out.get("backend") == "office-builtin"
