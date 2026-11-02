"""ToolNameAdapter — wire 名 ↔ 内部 Provider 名（tool:xxx）。"""

from __future__ import annotations

import pytest

from project_runtime.llm.tool_name_adapter import ToolNameAdapter


def test_colon_becomes_underscore_on_wire():
    ad = ToolNameAdapter()
    assert ad.to_wire("tool:read_file") == "tool_read_file"
    assert ad.to_wire("web_search") == "web_search"


def test_roundtrip_via_map():
    ad = ToolNameAdapter()
    wire = ad.to_wire("tool:write_file")
    assert wire == "tool_write_file"
    assert ad.to_internal(wire) == "tool:write_file"


def test_adapt_tools_rewrites_function_names():
    ad = ToolNameAdapter()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "search",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool:read_file",
                "description": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool:write_file",
                "description": "write",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    out = ad.adapt_tools(tools)
    names = [t["function"]["name"] for t in out]
    assert names == ["web_search", "tool_read_file", "tool_write_file"]
    assert tools[1]["function"]["name"] == "tool:read_file"
    assert ad.to_internal("tool_read_file") == "tool:read_file"
    assert ad.to_internal("tool_write_file") == "tool:write_file"


def test_adapt_tools_none_passthrough():
    ad = ToolNameAdapter()
    assert ad.adapt_tools(None) is None


def test_wire_names_are_gateway_safe():
    import re

    safe = re.compile(r"^[a-zA-Z0-9_.\-]+$")
    ad = ToolNameAdapter()
    for internal in (
        "tool:read_file",
        "tool:list_dir",
        "tool:office_pptx",
        "web_search",
    ):
        wire = ad.to_wire(internal)
        assert safe.match(wire), wire


def test_unknown_wire_name_returned_as_is():
    ad = ToolNameAdapter()
    assert ad.to_internal("tool_never_registered") == "tool_never_registered"


def test_style_colon_keeps_colon():
    ad = ToolNameAdapter(style="colon")
    assert ad.to_wire("tool:read_file") == "tool:read_file"


def test_style_dot():
    ad = ToolNameAdapter(style="dot")
    assert ad.to_wire("tool:read_file") == "tool.read_file"
    assert ad.to_internal("tool.read_file") == "tool:read_file"


def test_wire_collision_raises():
    ad = ToolNameAdapter(style="underscore")
    ad.to_wire("foo:bar")
    ad._map.wire_to_canonical["zz_yy"] = "aa:bb"
    with pytest.raises(ValueError, match="wire collision"):
        ad.to_wire("zz:yy")
