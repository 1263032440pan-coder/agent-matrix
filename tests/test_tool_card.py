"""工具卡合同（方案 §13.2 / TS-01）— TDD."""

from __future__ import annotations


def test_tool_card_from_legacy_schema_puts_description_in_summary():
    from project_runtime.worker.tool_card import tool_card_from_legacy
    from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

    card = tool_card_from_legacy("tool:read_file", TOOL_SCHEMAS["tool:read_file"])
    assert card["summary"]
    assert "parameters" in card
    assert card.get("when_not") in (None, "")
    assert card.get("detail") in (None, {}, "")


def test_compose_description_keeps_when_not_before_when():
    from project_runtime.worker.tool_card import compose_tool_description

    text = compose_tool_description(
        {
            "summary": "摘要工具",
            "when_not": "不要在写二进制时用",
            "when": "读文本时用",
        },
        budget=400,
    )
    assert text.index("不要在写二进制时用") < text.index("读文本时用")
    assert text.startswith("摘要工具")


def test_compose_description_drops_when_first_when_over_budget():
    from project_runtime.worker.tool_card import compose_tool_description

    card = {
        "summary": "S" * 80,
        "when_not": "N" * 120,
        "when": "W" * 120,
    }
    text = compose_tool_description(card, budget=220)
    assert "S" * 80 in text
    assert "N" * 120 in text
    assert "W" * 120 not in text
    assert len(text) <= 220


def test_tool_schema_for_prefers_provider_tool_card():
    from project_runtime.worker.tool_card import schema_from_tool_card

    schema = schema_from_tool_card(
        "tool:demo",
        {
            "summary": "演示",
            "when_not": "勿滥用",
            "when": "测试时用",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        },
    )
    assert schema["name"] == "tool:demo"
    assert "演示" in schema["description"]
    assert "勿滥用" in schema["description"]
    assert schema["parameters"]["required"] == ["x"]


def test_detail_sections_reserved_api_toc_and_section():
    """T4 / TS-10：预留读取接口，不注册 tool_help。"""
    from project_runtime.worker.tool_card import (
        list_detail_sections,
        read_detail_section,
    )

    card = {
        "detail": {
            "overview": "总览",
            "images": "图片路径须相对工作区",
        },
    }
    assert list_detail_sections(card) == ["overview", "images"]
    toc = read_detail_section(card, None)
    assert toc["ok"] and toc["mode"] == "toc"
    assert "overview" in toc["text"]
    body = read_detail_section(card, "images")
    assert body["ok"] and "相对工作区" in body["text"]
    bad = read_detail_section(card, "nope")
    assert bad["ok"] is False


def test_tool_help_not_registered_as_tool():
    from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

    assert "tool:tool_help" not in TOOL_SCHEMAS
    assert "tool_help" not in TOOL_SCHEMAS
