"""工具面构建器（方案 §13.3 / TS-02–TS-04）— TDD."""

from __future__ import annotations

import pytest

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry


class _FakeTool:
    def __init__(
        self,
        name: str,
        *,
        summary: str = "sum",
        when_not: str = "",
        when: str = "",
        healthy: bool = True,
        enabled: bool = True,
        priority: int = 100,
        needs_ws: bool = False,
        detail: dict | None = None,
        llm_name: str | None = None,
    ):
        self.name = name
        self.capability = CapabilityType.TOOL
        self.permission = "READ_ONLY"
        self.priority = priority
        self.enabled = enabled
        self._healthy = healthy
        self._needs_ws = needs_ws
        self._llm_name = llm_name
        self._card = {
            "summary": summary,
            "when_not": when_not,
            "when": when,
            "parameters": {"type": "object", "properties": {}},
            "detail": detail or {},
            "needs_workspace_root": needs_ws,
        }

    def is_usable(self, registry=None) -> bool:
        return self._healthy

    async def health(self) -> bool:
        return self._healthy

    def tool_card(self) -> dict:
        return self._card

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_card import schema_from_tool_card

        return schema_from_tool_card(
            self.name, self._card, llm_name=self._llm_name,
        )

    async def invoke(self, payload: dict) -> dict:
        return {"ok": True}


class _FakeResearch:
    name = "search:stub"
    capability = CapabilityType.RESEARCH
    permission = "READ_ONLY"
    priority = 50
    enabled = True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        return []


@pytest.fixture
def reg():
    return ExtensionRegistry()


def _names(built) -> set[str]:
    return {t["function"]["name"] for t in (built.get("tools") or [])}


def test_surface_skips_unhealthy(reg):
    from project_runtime.worker.tool_surface import build_tool_surface

    reg.register(_FakeTool("tool:ok", summary="好用"))
    reg.register(_FakeTool("tool:bad", summary="坏掉", healthy=False))
    built = build_tool_surface(reg, whitelist=None)
    assert _names(built) == {"tool:ok"}
    assert any(
        f.get("reason") == "UNHEALTHY"
        for f in built["snapshot"]["filtered"]
        if f.get("name") == "tool:bad"
    )


def test_surface_whitelist_and_empty(reg):
    from project_runtime.worker.tool_surface import build_tool_surface

    reg.register(_FakeTool("tool:write_file", summary="写"))
    reg.register(_FakeTool("tool:shell", summary="壳"))
    built = build_tool_surface(reg, whitelist=["write_file"])
    assert _names(built) == {"tool:write_file"}
    assert build_tool_surface(reg, whitelist=[])["tools"] == []


def test_surface_web_search_from_tool_facade_not_inline(reg):
    from project_runtime.worker.tool_surface import build_tool_surface
    from project_runtime.worker.web_search_tool import WebSearchToolProvider

    reg.register(_FakeResearch())
    reg.register(WebSearchToolProvider())
    built = build_tool_surface(reg, whitelist=["web_search"])
    assert _names(built) == {"web_search"}
    # 无 RESEARCH → 门面 health 失败 → 不可见
    reg2 = ExtensionRegistry()
    reg2.register(WebSearchToolProvider())
    built2 = build_tool_surface(reg2, whitelist=["web_search"])
    assert built2["tools"] == [] or "web_search" not in _names(built2)


def test_compose_budget_block_drop(reg):
    from project_runtime.worker.tool_surface import build_tool_surface

    reg.register(
        _FakeTool(
            "tool:big",
            summary="S" * 80,
            when_not="N" * 120,
            when="W" * 120,
        )
    )
    built = build_tool_surface(reg, whitelist=None, desc_budget=220)
    desc = built["tools"][0]["function"]["description"]
    assert "W" * 120 not in desc
    assert "N" * 120 in desc


def test_card_only_provider_appears_without_tool_schemas(reg):
    """V15: 只带 tool_card、不在 TOOL_SCHEMAS → 仍可见。"""
    from project_runtime.worker.tool_surface import build_tool_surface

    class OnlyCard:
        name = "tool:only_card"
        capability = CapabilityType.TOOL
        permission = "READ_ONLY"
        priority = 10
        enabled = True

        def is_usable(self, registry=None) -> bool:
            return True

        async def health(self) -> bool:
            return True

        def tool_card(self) -> dict:
            return {
                "summary": "仅卡片",
                "when_not": "",
                "when": "",
                "parameters": {"type": "object", "properties": {}},
            }

        async def invoke(self, payload: dict) -> dict:
            return {"ok": True}

    reg.register(OnlyCard())
    built = build_tool_surface(reg, whitelist=["only_card"])
    assert _names(built) == {"tool:only_card"}


def test_obligation_orders_and_builds_manual(reg):
    """V17：义务相关工具靠前；手册含 detail。"""
    from project_runtime.worker.tool_surface import (
        TOOL_MANUAL_MARKER,
        build_tool_surface,
    )

    reg.register(_FakeTool("tool:shell", summary="壳", priority=10))
    reg.register(
        _FakeTool(
            "tool:office_pptx",
            summary="PPT",
            priority=5,
            detail={"overview": "用 JSON spec 生成 pptx"},
        )
    )
    built = build_tool_surface(
        reg, whitelist=None, obligation_plugins={"office_pptx"},
    )
    names = [t["function"]["name"] for t in built["tools"]]
    assert names[0] == "tool:office_pptx"
    assert TOOL_MANUAL_MARKER in built["manual"]
    assert "office_pptx" in built["manual"]
    assert "JSON spec" in built["manual"]
    assert built["snapshot"]["manual"][0]["source"] == "OBLIGATION"


def test_no_obligation_no_office_manual(reg):
    """V18：无办公义务 → 手册不含办公 detail。"""
    from project_runtime.worker.tool_surface import build_tool_surface

    reg.register(
        _FakeTool(
            "tool:office_pptx",
            summary="PPT",
            detail={"overview": "秘密规程"},
        )
    )
    built = build_tool_surface(reg, whitelist=None, obligation_plugins=set())
    assert "秘密规程" not in (built["manual"] or "")
    assert built["snapshot"]["manual"] == []
