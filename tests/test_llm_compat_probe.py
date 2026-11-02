"""LLM gateway compat fingerprint + probe selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_runtime.llm import LLMSettings, load_llm_settings, save_llm_settings
from project_runtime.llm.compat import (
    CompatError,
    GatewayCompat,
    build_compat,
    compat_valid,
    is_invalid_function_name_error,
    require_tools_compat,
    settings_fingerprint,
)


def test_fingerprint_changes_with_model():
    a = settings_fingerprint("http://x", "m1", "k")
    b = settings_fingerprint("http://x", "m2", "k")
    assert a != b


def test_compat_valid_false_when_fingerprint_mismatch():
    cfg = LLMSettings(
        base_url="http://x",
        model="m1",
        api_key="k",
        compat=GatewayCompat(
            ok=True,
            chat_ok=True,
            tools_supported=True,
            tool_name_style="underscore",
            fingerprint=settings_fingerprint("http://x", "m0", "k"),
        ),
    )
    assert compat_valid(cfg) is False


def test_compat_valid_true_when_match():
    fp = settings_fingerprint("http://x", "m1", "k")
    cfg = LLMSettings(
        base_url="http://x",
        model="m1",
        api_key="k",
        compat=GatewayCompat(
            ok=True,
            chat_ok=True,
            tools_supported=True,
            tool_name_style="underscore",
            fingerprint=fp,
        ),
    )
    assert compat_valid(cfg) is True


def test_require_tools_compat_raises_without_compat():
    cfg = LLMSettings(base_url="http://x", model="m", api_key="k")
    with pytest.raises(CompatError, match="重新测试"):
        require_tools_compat(cfg)


def test_require_tools_compat_raises_when_tools_unsupported():
    fp = settings_fingerprint("http://x", "m", "k")
    cfg = LLMSettings(
        base_url="http://x",
        model="m",
        api_key="k",
        compat=GatewayCompat(
            ok=True,
            chat_ok=True,
            tools_supported=False,
            tool_name_style="none",
            fingerprint=fp,
        ),
    )
    with pytest.raises(CompatError, match="不支持工具"):
        require_tools_compat(cfg)


def test_is_invalid_function_name_error():
    assert is_invalid_function_name_error(
        'Validation: Function at index 1 has an invalid name: "tool:read_file". '
        "Only a-z, A-Z, 0-9, underscores, dashes, and dots are allowed."
    )
    assert not is_invalid_function_name_error("Connection timeout")


@pytest.mark.asyncio
async def test_probe_prefers_underscore_when_colon_rejected(tmp_path, monkeypatch):
    import project_runtime.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_CONFIG_PATH", tmp_path / "llm_settings.json")

    calls: list[str] = []

    class _Msg:
        content = "pong"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_completion(**kwargs):
        tools = kwargs.get("tools")
        if not tools:
            return _Resp()
        name = tools[0]["function"]["name"]
        calls.append(name)
        if ":" in name:
            raise Exception(
                'BadRequestError: invalid name: "tool:probe_ping". '
                "Only a-z, A-Z, 0-9, underscores, dashes, and dots are allowed."
            )
        return _Resp()

    import sys
    import types

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.completion = fake_completion
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    cfg = LLMSettings(
        base_url="http://gw/v1", model="openai/m", api_key="secret",
    )
    result = await llm_mod.test_llm_connection(cfg)
    assert result["ok"] is True
    assert result["compat"]["tool_name_style"] == "underscore"
    assert result["compat"]["tools_supported"] is True
    saved = load_llm_settings()
    assert compat_valid(saved)
    assert saved.compat.tool_name_style == "underscore"
    assert "tool:probe_ping" in calls
    assert "tool_probe_ping" in calls


@pytest.mark.asyncio
async def test_probe_both_ok_writes_underscore(tmp_path, monkeypatch):
    import project_runtime.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_CONFIG_PATH", tmp_path / "llm_settings.json")

    class _Msg:
        content = "pong"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_completion(**kwargs):
        return _Resp()

    import sys
    import types

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.completion = fake_completion
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    cfg = LLMSettings(base_url="http://gw/v1", model="m", api_key="k")
    result = await llm_mod.test_llm_connection(cfg)
    assert result["compat"]["tool_name_style"] == "underscore"


@pytest.mark.asyncio
async def test_probe_no_tools_supported(tmp_path, monkeypatch):
    import project_runtime.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_CONFIG_PATH", tmp_path / "llm_settings.json")

    class _Msg:
        content = "pong"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_completion(**kwargs):
        if kwargs.get("tools"):
            raise Exception("BadRequestError: tools not supported")
        return _Resp()

    import sys
    import types

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.completion = fake_completion
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    cfg = LLMSettings(base_url="http://gw/v1", model="m", api_key="k")
    result = await llm_mod.test_llm_connection(cfg)
    assert result["ok"] is True
    assert result["compat"]["tools_supported"] is False
    assert result["compat"]["tool_name_style"] == "none"


def test_build_compat_roundtrip_save(tmp_path, monkeypatch):
    import project_runtime.llm as llm_mod

    path = tmp_path / "llm_settings.json"
    monkeypatch.setattr(llm_mod, "_CONFIG_PATH", path)
    cfg = LLMSettings(base_url="http://x", model="m", api_key="k")
    cfg.compat = build_compat(
        cfg=cfg,
        chat_ok=True,
        tools_supported=True,
        tool_name_style="underscore",
        notes="ok",
    )
    save_llm_settings(cfg)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["compat"]["tool_name_style"] == "underscore"
    loaded = load_llm_settings()
    assert loaded.compat is not None
    assert compat_valid(loaded)
