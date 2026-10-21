"""V1.4h 工具循环修复测试: 软收敛注入 + 强收摘要。"""

import json

import pytest


def _make_worker():
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._max_tokens = 16000
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


class TestSoftConvergence:
    @pytest.mark.asyncio
    async def test_soft_nudge_injected_at_round_8(self, monkeypatch):
        """第 _TOOL_ROUNDS-4 轮后 messages 应注入系统提醒（只一次）。"""
        from project_runtime.worker.llm_worker import _TOOL_ROUNDS

        w = _make_worker()
        seen_prompts = []

        async def fake_provider(**kwargs):
            msgs = kwargs.get("messages") or []
            seen_prompts.append([m.get("content") or "" for m in msgs])
            # 第 8 轮后停止调工具（模拟模型听从提醒）
            if len(seen_prompts) >= _TOOL_ROUNDS - 4 + 1:
                yield _FakeChunk(_FakeDelta(content="最终交付内容"))
                return
            tc = _FakeTC(0, f"c{len(seen_prompts)}", "tool:todo",
                         json.dumps({"action": "list"}))
            yield _FakeChunk(_FakeDelta(tool_calls=[tc]))

        _patch_llm(monkeypatch, fake_provider)
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
        assert '最终交付内容' in out  # 模型听从提醒正常收敛
        # 提醒在第 8 轮的 messages 里出现（第 9 轮请求可见）
        assert any(
            any("系统提醒" in c for c in prompts)
            for prompts in seen_prompts[_TOOL_ROUNDS - 4:]
        ), "第 8 轮后应见软收敛提醒"

    @pytest.mark.asyncio
    async def test_force_close_with_tool_digest(self, monkeypatch):
        """12 轮全 tool_call → 强收带工具摘要（非空话）。"""
        from project_runtime.worker.llm_worker import _TOOL_ROUNDS

        w = _make_worker()
        n = {"i": 0}

        async def fake_provider(**kwargs):
            n["i"] += 1
            tc = _FakeTC(0, f"c{n['i']}", "tool:todo",
                         json.dumps({"action": "list"}))
            yield _FakeChunk(_FakeDelta(tool_calls=[tc]))

        _patch_llm(monkeypatch, fake_provider)
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
        assert _TOOL_ROUNDS == 12
        assert "已执行" in out and "次工具调用" in out
        assert "未能收敛出最终交付内容" in out
        # 空话版文案不应出现（有工具历史时）
        assert out != "（工具循环达上限，未能产出最终内容）"
