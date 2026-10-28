"""工具循环: 32 轮预算 + 删软闸 + 预算用尽 Continue nudge / Grace / Stop。"""

import json

import pytest


def _make_worker():
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._max_tokens = 16000
    w._delivery_repair_counts = {}
    w._current_project_id = None
    w._session = None
    w._current_task_id = 1
    w._workspace_root = None
    w._artifact_mgr = None
    w._db_lock = None
    return w


class _FakeFn:
    def __init__(self, name):
        self.name = name


class _FakeTC:
    def __init__(self, idx, cid, name, args):
        self.index = idx
        self.id = cid
        self.function = _FakeFn(name)
        self.function.arguments = args


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = None


class _FakeChunk:
    def __init__(self, delta):
        self.choices = [type("C", (), {"delta": delta})()]


_FAKE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "tool:todo",
        "description": "todo",
        "parameters": {"type": "object", "properties": {}},
    },
}]


def _patch_llm(monkeypatch, fake):
    import sys

    class _Provider:
        @staticmethod
        async def invoke(payload=None, **kwargs):
            class _R:
                async def __aiter__(self):
                    merged = {**(payload or {}), **kwargs}
                    async for chunk in fake(**merged):
                        yield chunk

            return {"ok": True, "response": _R()}

    fake_mod = type(sys)("fake")
    fake_mod.llm_acomplete = _Provider.invoke
    monkeypatch.setitem(sys.modules, "project_runtime.llm.provider", fake_mod)


def _stub_tools(monkeypatch, worker):
    monkeypatch.setattr(
        worker, "_search_tools", lambda whitelist=None: list(_FAKE_TOOLS),
    )

    async def _noop_tool(*a, **k):
        return ("ok", True)

    monkeypatch.setattr(worker, "_run_search_tool", _noop_tool)


class TestNoSoftGate:
    @pytest.mark.asyncio
    async def test_no_soft_convergence_reminder(self, monkeypatch):
        """不得再注入「【系统提醒】工具调用已进行 N 轮」。"""
        from project_runtime.worker.llm_worker import _TOOL_ROUNDS

        w = _make_worker()
        seen_prompts = []

        async def fake_provider(**kwargs):
            msgs = kwargs.get("messages") or []
            seen_prompts.append([m.get("content") or "" for m in msgs])
            # 提前在第 3 轮收工（无工具）
            if len(seen_prompts) >= 3:
                yield _FakeChunk(_FakeDelta(content="最终交付内容"))
                return
            tc = _FakeTC(0, f"c{len(seen_prompts)}", "tool:todo",
                         json.dumps({"action": "list"}))
            yield _FakeChunk(_FakeDelta(tool_calls=[tc]))

        _patch_llm(monkeypatch, fake_provider)
        _stub_tools(monkeypatch, w)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k",
                "model": "m"})(),
        )

        class _WP:
            task_id = 1
            task_name = "T"
            playbook_ref = None
            contract = None
            skill_context = None
            tool_whitelist = None
            skill_whitelist = None

        out = await w._execute_llm(_WP())
        assert "最终交付内容" in out
        assert _TOOL_ROUNDS == 32
        flat = "\n".join(c for prompts in seen_prompts for c in prompts)
        assert "【系统提醒】" not in flat
        assert "如果已有足够信息" not in flat


class TestBudgetGrace:
    @pytest.mark.asyncio
    async def test_budget_exhaust_grace_then_stop_ok(self, monkeypatch):
        """32 轮全 tool → Continue nudge + Grace（无 tools）→ Stop 通过 → 归档。"""
        from project_runtime.worker.llm_worker import _TOOL_ROUNDS

        w = _make_worker()
        n = {"i": 0}
        grace_seen = {"v": False}

        async def fake_provider(**kwargs):
            n["i"] += 1
            tools = kwargs.get("tools")
            msgs = kwargs.get("messages") or []
            flat = " ".join(str(m.get("content") or "") for m in msgs)
            if "工具轮次已用尽" in flat or "不要再调用工具" in flat:
                grace_seen["v"] = True
                assert not tools, "Grace 轮不得带 tools"
                yield _FakeChunk(_FakeDelta(content="Grace收尾说明"))
                return
            if n["i"] > _TOOL_ROUNDS:
                yield _FakeChunk(_FakeDelta(content="unexpected"))
                return
            tc = _FakeTC(0, f"c{n['i']}", "tool:todo",
                         json.dumps({"action": "list"}))
            yield _FakeChunk(_FakeDelta(tool_calls=[tc]))

        _patch_llm(monkeypatch, fake_provider)
        _stub_tools(monkeypatch, w)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k",
                "model": "m"})(),
        )

        async def _stop(_wp):
            return []

        monkeypatch.setattr(w, "_delivery_stop_problems", _stop)

        class _WP:
            task_id = 1
            task_name = "T"
            playbook_ref = None
            contract = None
            skill_context = None
            tool_whitelist = None
            skill_whitelist = None

        out = await w._execute_llm(_WP())
        assert _TOOL_ROUNDS == 32
        assert grace_seen["v"], "预算用尽后应注入 Continue nudge 并跑 Grace"
        assert "Grace收尾说明" in out
        assert n["i"] == _TOOL_ROUNDS + 1  # 32 tools + 1 grace

    @pytest.mark.asyncio
    async def test_budget_exhaust_stop_fail_raises(self, monkeypatch):
        """Grace 后 Stop 失败 → DeliveryBaselineError（不再工具返工）。"""
        from project_runtime.worker.llm_worker import (
            DeliveryBaselineError,
            _TOOL_ROUNDS,
        )

        w = _make_worker()
        n = {"i": 0}

        async def fake_provider(**kwargs):
            n["i"] += 1
            tools = kwargs.get("tools")
            if not tools:
                yield _FakeChunk(_FakeDelta(content="Grace但仍缺件"))
                return
            tc = _FakeTC(0, f"c{n['i']}", "tool:todo",
                         json.dumps({"action": "list"}))
            yield _FakeChunk(_FakeDelta(tool_calls=[tc]))

        _patch_llm(monkeypatch, fake_provider)
        _stub_tools(monkeypatch, w)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k",
                "model": "m"})(),
        )

        async def _stop(_wp):
            return ["缺 README"]

        monkeypatch.setattr(w, "_delivery_stop_problems", _stop)

        class _WP:
            task_id = 1
            task_name = "T"
            playbook_ref = None
            contract = None
            skill_context = None
            tool_whitelist = None
            skill_whitelist = None

        with pytest.raises(DeliveryBaselineError) as ei:
            await w._execute_llm(_WP())
        assert "缺 README" in str(ei.value) or "用尽" in str(ei.value)
        assert n["i"] == _TOOL_ROUNDS + 1
