"""V1.6b: 交付收工硬闸（Claude Stop 对齐）——存在性校验 + 3 轮返工 + FAILED。"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_runtime.worker.llm_worker import (
    DeliveryBaselineError,
    LLMWorkerAdapter,
    _check_delivery_workspace,
    _delivery_tags_for_wp,
)


class _WP:
    def __init__(self, *, tags=None, code_stage=False, task_id=7):
        self.task_id = task_id
        self.delivery_tags = tags
        self.code_stage = code_stage
        self.playbook_ref = None
        self.contract = None
        self.skill_context = None
        self.tool_whitelist = None
        self.task_name = "t"
        self.workspace_root = None


def test_tags_from_wp_and_code_stage():
    assert _delivery_tags_for_wp(_WP(tags=["ppt"])) == ["ppt"]
    assert "code" in _delivery_tags_for_wp(_WP(code_stage=True))
    assert _delivery_tags_for_wp(_WP()) == []


def test_check_missing_ppt(tmp_path: Path):
    problems = _check_delivery_workspace(tmp_path, ["ppt"])
    assert any("pptx" in p.lower() or "PPT" in p for p in problems)


def test_check_code_needs_readme(tmp_path: Path):
    (tmp_path / "main.py").write_text("x=1", encoding="utf-8")
    problems = _check_delivery_workspace(tmp_path, ["code"])
    assert any("README" in p or "readme" in p.lower() for p in problems)
    (tmp_path / "README.md").write_text("# t", encoding="utf-8")
    assert _check_delivery_workspace(tmp_path, ["code"]) == []


def test_check_test_report(tmp_path: Path):
    problems = _check_delivery_workspace(tmp_path, ["test_report"])
    assert problems
    (tmp_path / "测试报告.md").write_text("ok", encoding="utf-8")
    assert _check_delivery_workspace(tmp_path, ["test_report"]) == []


@pytest.mark.asyncio
async def test_stop_gate_blocks_then_passes(tmp_path, monkeypatch):
    """拟终稿时缺件 → 注入返工；补齐后放行。"""

    ws = tmp_path / "data" / "tasks" / "1" / "7"
    ws.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    rounds = {"n": 0}

    class _FakeStream:
        def __init__(self, text: str, tools=None):
            self._text = text
            self._tools = tools or []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_done"):
                self._done = True

                class D:
                    content = self._text
                    reasoning_content = None
                    tool_calls = self._tools

                class C:
                    delta = D()

                class Chunk:
                    choices = [C()]

                return Chunk()
            raise StopAsyncIteration

    async def fake_stream(self, cfg, model, messages, tools):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return {"ok": True, "response": _FakeStream("draft without ppt")}
        # after repair injection, model "fixes" by writing file then final
        (ws / "方案.pptx").write_bytes(b"PK\x03\x04fake")
        return {"ok": True, "response": _FakeStream("final with ppt")}

    monkeypatch.setattr(LLMWorkerAdapter, "_stream_round", fake_stream)
    monkeypatch.setattr(
        "project_runtime.llm.load_llm_settings",
        lambda: type("C", (), {
            "base_url": "http://x", "api_key": "k", "model": "m",
            "provider": "openai",
        })(),
    )
    monkeypatch.setattr(
        "project_runtime.llm.normalize_model_for_gateway",
        lambda m, b: m,
    )

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._session = None
    w._current_task_id = 7
    w._current_project_id = 1
    w._current_agent = None
    w._nudged_tasks = set()
    w._delivery_repair_counts = {}
    w._max_tokens = 1000
    w._artifact_mgr = type("M", (), {"_workspace_root": "data/tasks"})()
    w._search_tools = lambda whitelist=None: None

    async def _arch(task_id, wp, store, content, stats):
        return content

    w._archive_session = _arch

    wp = _WP(tags=["ppt"], task_id=7)
    wp.workspace_root = str(ws)
    # bind workspace helper
    w._task_ws_root = lambda: ws

    out = await w._execute_llm(wp)
    assert "final with ppt" in out
    assert rounds["n"] == 2
    assert (ws / "方案.pptx").exists()


@pytest.mark.asyncio
async def test_stop_gate_fails_after_3_repairs(tmp_path, monkeypatch):
    """3 次返工仍缺 → DeliveryBaselineError。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(tmp_path)

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_done"):
                self._done = True

                class D:
                    content = "still missing"
                    reasoning_content = None
                    tool_calls = []

                class C:
                    delta = D()

                class Chunk:
                    choices = [C()]

                return Chunk()
            raise StopAsyncIteration

    async def fake_stream(self, cfg, model, messages, tools):
        return {"ok": True, "response": _FakeStream()}

    monkeypatch.setattr(LLMWorkerAdapter, "_stream_round", fake_stream)
    monkeypatch.setattr(
        "project_runtime.llm.load_llm_settings",
        lambda: type("C", (), {
            "base_url": "http://x", "api_key": "k", "model": "m",
            "provider": "openai",
        })(),
    )
    monkeypatch.setattr(
        "project_runtime.llm.normalize_model_for_gateway",
        lambda m, b: m,
    )

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._session = None
    w._current_task_id = 7
    w._current_project_id = 1
    w._current_agent = None
    w._nudged_tasks = set()
    w._delivery_repair_counts = {}
    w._max_tokens = 1000
    w._artifact_mgr = None
    w._search_tools = lambda whitelist=None: None
    w._task_ws_root = lambda: ws

    async def _arch(task_id, wp, store, content, stats):
        return content

    w._archive_session = _arch

    with pytest.raises(DeliveryBaselineError) as ei:
        await w._execute_llm(_WP(tags=["ppt"], task_id=7))
    assert "3" in str(ei.value) or "返工" in str(ei.value)
