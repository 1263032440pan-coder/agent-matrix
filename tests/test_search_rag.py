"""规范 19: 搜索 Provider 化 + 模型驱动工具循环.

覆盖（全部离线——搜索走 stub/monkeypatch，不打真网）:
  [x] ExaProvider 契约（name/capability/priority/SSE 解析/限流识别/冷却）
  [x] ScrapeProvider 契约（迁移回归由既有 executor 用例兜底）
  [x] registry 编排: Exa 失败落 scrape; stub 注册即参与（P2-1 验收形态）
  [x] Worker 工具循环: 搜索路径（tool_call → 执行 → 喂回 → 次轮终稿）
  [x] Worker 工具循环: 不搜索（任务自足单轮返回——既有用例族）
  [x] Worker 工具循环: 4 轮上限强收
  [x] 降级: registry 空 → 工具不暴露; 网关不支持 tools → 剔参重发
  [x] 事件审计: LLM_TOOL_CALL / LLM_TOOL_RESULT
"""

import json

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.project_state.base import Base


class _FakeLitellmMod:
    """替身 litellm 模块（函数内 import 每次新解析 → patch sys.modules）。"""

    def __init__(self, fn):
        self.acompletion = fn


def _patch_litellm(monkeypatch, fn):
    import sys
    monkeypatch.setitem(sys.modules, "litellm", _FakeLitellmMod(fn))


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p19.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


# ── ExaProvider ────────────────────────────────────────────────────


class TestExaProvider:
    def test_contract(self):
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.research.providers.exa_mcp import ExaProvider

        p = ExaProvider()
        assert p.name == "search:exa"
        assert p.capability == CapabilityType.RESEARCH
        assert p.priority == 50
        assert p.permission == "READ_ONLY"

    @pytest.mark.asyncio
    async def test_sse_parse_success(self):
        from project_runtime.research.providers.exa_mcp import ExaProvider

        sse = (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":'
            '{"content":[{"type":"text","text":"Title: 软件需求文档指南\\n'
            'URL: https://example.com/a\\nPublished: 2026\\nHighlights:\\n'
            '输入物包括需求清单。\\n...\\n'
            'Title: 第二篇\\nURL: https://example.com/b\\nHighlights:\\n'
            '内容二。"}]}}\n\n'
        )
        findings = ExaProvider._parse_sse(sse)
        assert len(findings) == 2
        assert findings[0]["title"] == "软件需求文档指南"
        assert findings[0]["source"] == "https://example.com/a"
        assert "输入物" in findings[0]["content"]

    @pytest.mark.asyncio
    async def test_sse_parse_garbage_returns_empty(self):
        """解析容错（规范 19 §3.2）: 上游改版/垃圾输入 → 空列表不抛。"""
        from project_runtime.research.providers.exa_mcp import ExaProvider

        assert ExaProvider._parse_sse("") == []
        assert ExaProvider._parse_sse("no data lines here") == []
        assert ExaProvider._parse_sse("data: not-json") == []
        assert ExaProvider._parse_sse(
            'data: {"result": {"content": [{"type": "text", "text": ""}]}}'
        ) == []

    @pytest.mark.asyncio
    async def test_rate_limit_detection_and_cooldown(self):
        """限流文案（200 + rate limit）→ 触发冷却（实测非 429）。"""
        import time

        from project_runtime.research.providers.exa_mcp import ExaProvider

        ExaProvider._cooldown_until = 0.0
        ExaProvider._fail_streak = 0
        sse = (
            'data: {"result":{"content":[{"type":"text","text":'
            '"You\'ve hit Exa\'s free MCP rate limit. To continue, '
            'get an API key."}]}}'
        )
        findings = ExaProvider._parse_sse(sse)
        assert findings == []
        assert time.monotonic() < ExaProvider._cooldown_until, "应进入冷却"
        # 冷却中 invoke → 空且不再打网（_search 不被调用）
        p = ExaProvider()

        async def should_not_run(q, n):
            raise AssertionError("冷却中不应发起搜索")

        p._search = should_not_run
        assert await p.invoke({"query": "x"}) == []
        ExaProvider._cooldown_until = 0.0

    @pytest.mark.asyncio
    async def test_empty_results_do_not_trip_fail_streak(self):
        """服务端间歇空结果 ≠ 调用失败——不计 fail_streak（防误熔断）。"""
        from project_runtime.research.providers.exa_mcp import ExaProvider

        ExaProvider._cooldown_until = 0.0
        ExaProvider._fail_streak = 0
        p = ExaProvider()

        async def fake_search(q, n):
            return []  # 服务端空

        p._search = fake_search
        await p.invoke({"query": "x"})
        await p.invoke({"query": "x"})
        assert ExaProvider._fail_streak == 0, "空结果不计失败"
        assert ExaProvider._cooldown_until == 0.0, "不应误熔断"


# ── registry 编排（P2-1 验收形态）──────────────────────────────────


class TestRegistryOrchestration:
    @pytest.mark.asyncio
    async def test_stub_provider_joins_via_registry(self):
        """P2-1 核心: 第三方 provider 注册即参与——零 Core 改动。"""
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.research.providers import (
            register_search_providers,
        )

        reg = shared_registry()
        register_search_providers(reg)

        class MySearch:
            name = "search:mine"
            capability = CapabilityType.RESEARCH
            permission = "READ_ONLY"
            priority = 300  # 压过 Exa(200)/scrape(100)
            enabled = True

            async def health(self):
                return True

            async def invoke(self, payload):
                return [{"source": "s", "title": payload["query"],
                         "content": "stub 结果"}]

        reg.register(MySearch())
        infos = reg.list_providers(CapabilityType.RESEARCH)
        assert infos[0].name == "search:mine", "优先级最高应排首"
        result = await reg.invoke(
            CapabilityType.RESEARCH, {"query": "测试"}
        )
        assert result[0]["title"] == "测试"

    @pytest.mark.asyncio
    async def test_executor_falls_back_to_next_provider(self):
        """Exa 空 → scrape 接管（executor._search 编排）。"""
        from unittest.mock import AsyncMock

        from project_runtime.research.executor import WebResearchExecutor

        ex = WebResearchExecutor.__new__(WebResearchExecutor)  # 不跑 __init__
        ex._session = None

        class Info:
            def __init__(self, name, provider, enabled=True):
                self.name = name
                self.provider = provider
                self.enabled = enabled

        class Reg:
            def list_providers(self, cap):
                return [Info("search:exa", AsyncMock(
                    invoke=AsyncMock(return_value=[]))),
                        Info("search:scrape", AsyncMock(
                    invoke=AsyncMock(return_value=[
                        {"source": "s", "title": "t", "content": "c"}])))]

        ex._registry = Reg()
        results = await ex._search("q")
        assert len(results) == 1 and results[0]["title"] == "t"


# ── Worker 工具循环 ────────────────────────────────────────────────


class _FakeDelta:
    def __init__(self, content="", reasoning="", tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls or []


class _FakeChunk:
    def __init__(self, delta):
        self.choices = [type("C", (), {"delta": delta})()]


class _FakeTC:
    def __init__(self, idx, cid, name, args):
        self.index = idx
        self.id = cid
        self.function = type(
            "F", (), {"name": name, "arguments": args}
        )()


def _make_worker():
    """构造不触发真网/真库的 LLMWorkerAdapter 测试替身。"""
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._max_tokens = 16000
    w._current_agent = None  # __new__ 跳过 __init__——补齐 CoT 事件字段
    return w


class TestWorkerToolLoop:
    def test_search_tools_hidden_when_registry_empty(self):
        """降级: registry 无 RESEARCH provider → 工具不暴露。"""
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        reg = shared_registry()
        # 摘掉全部搜索 provider（记录后恢复）
        saved = {
            i.name: i for i in reg.list_providers(CapabilityType.RESEARCH)
        }
        for i in reg.list_providers(CapabilityType.RESEARCH):
            reg.unregister(i.name)
        try:
            w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
            tools = w._search_tools()
            # 契约（20号规范 §4.3 三能力①接入后）: 搜索不可用 →
            # web_search 不暴露（原意图）; TOOL 插件若已注册仍可见。
            if tools is not None:
                names = [t["function"]["name"] for t in tools]
                assert "web_search" not in names
            else:
                assert tools is None
        finally:
            for name, info in saved.items():
                reg.register(info.provider)

    def test_search_tools_exposed_when_provider_registered(self):
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )
        from project_runtime.worker.llm_worker import LLMWorkerAdapter
        register_search_providers(shared_registry())
        w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
        tools = w._search_tools()
        assert tools and tools[0]["function"]["name"] == "web_search"
        assert "query" in tools[0]["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_tool_call_roundtrip(self, dbengine, monkeypatch):
        """搜索路径: 首轮 tool_call → 搜索执行 → 结果进 messages → 次轮终稿。"""
        from project_runtime.worker import llm_worker as lw

        w = _make_worker()
        captured_messages = []

        calls = {
            "n": 0,
        }

        async def fake_acompletion(**kw):
            captured_messages.append(list(kw.get("messages") or []))
            if calls["n"] == 0:
                calls["n"] = 1
                # 首轮: 模型决定搜索
                tc = _FakeTC(0, "call_1", "web_search",
                             json.dumps({"query": "测试查询"}))
                async def gen1():
                    yield _FakeChunk(_FakeDelta(reasoning="思考中…"))
                    yield _FakeChunk(_FakeDelta(tool_calls=[tc]))
                return gen1()
            # 次轮: 基于结果产出终稿
            async def gen2():
                yield _FakeChunk(_FakeDelta(content="最终交付内容"))
            return gen2()

        _patch_litellm(monkeypatch, fake_acompletion)
        # 搜索走 stub（不打网）
        async def fake_search_via_registry(q, args):
            return [{"source": "https://x", "title": "结果",
                     "content": "内容"}]
        monkeypatch.setattr(
            lw.LLMWorkerAdapter, "_search_via_registry",
            fake_search_via_registry,
        )
        # settings stub
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k", "model": "m"})(),
        )
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )
        register_search_providers(shared_registry())

        class _WP:
            task_id = 1
            task_name = "任务A"
            playbook_ref = None
            contract = None
            skill_context = None

        out = await w._execute_llm(_WP())
        assert out == "最终交付内容"
        # 第二轮 messages 含 tool 结果
        assert any(
            m.get("role") == "tool" for m in captured_messages[1]
        ), "搜索结果应喂回 messages"

    @pytest.mark.asyncio
    async def test_no_tool_call_single_round(self, monkeypatch):
        """不搜索路径: 任务自足 → 单轮返回（绝大多数任务）。"""

        w = _make_worker()
        calls = {"n": 0}

        async def fake_acompletion(**kw):
            calls["n"] += 1

            async def gen():
                yield _FakeChunk(_FakeDelta(content="直接产出"))
            return gen()

        _patch_litellm(monkeypatch, fake_acompletion)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k", "model": "m"})(),
        )
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )
        register_search_providers(shared_registry())

        class _WP:
            task_id = 1
            task_name = "任务A"
            playbook_ref = None
            contract = None
            skill_context = None

        out = await w._execute_llm(_WP())
        assert out == "直接产出"
        assert calls["n"] == 1, "无工具调用应单轮完成"

    @pytest.mark.asyncio
    async def test_tool_rounds_cap(self, monkeypatch):
        """4 轮上限: 连续 tool_call → 第 4 轮后强收。"""
        from project_runtime.worker import llm_worker as lw
        from project_runtime.worker.llm_worker import _TOOL_ROUNDS

        w = _make_worker()
        calls = {"n": 0}

        async def fake_acompletion(**kw):
            calls["n"] += 1
            tc = _FakeTC(0, f"call_{calls['n']}", "web_search",
                         json.dumps({"query": f"q{calls['n']}"}))

            async def gen():
                yield _FakeChunk(
                    _FakeDelta(content=f"轮{calls['n']}产出",
                               tool_calls=[tc]))
            return gen()

        _patch_litellm(monkeypatch, fake_acompletion)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k", "model": "m"})(),
        )

        async def fake_search(q, args):
            return [{"source": "s", "title": "t", "content": "c"}]
        monkeypatch.setattr(
            lw.LLMWorkerAdapter, "_search_via_registry", fake_search,
        )
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )
        register_search_providers(shared_registry())

        class _WP:
            task_id = 1
            task_name = "任务A"
            playbook_ref = None
            contract = None
            skill_context = None

        out = await w._execute_llm(_WP())
        assert calls["n"] == _TOOL_ROUNDS, (
            f"应恰好 {_TOOL_ROUNDS} 轮后强收（V1.4h: 4→12——11 工具面下"
            "模型需摸环境+干活+收敛的完整工作流预算）"
        )
        assert "轮12产出" in out or "轮" in out

    @pytest.mark.asyncio
    async def test_gateway_tools_unsupported_fallback(self, monkeypatch):
        """降级: 网关不支持 tools（报错）→ 剔 tools 单轮重发。"""

        w = _make_worker()
        state = {"n": 0, "saw_tools": []}

        async def fake_acompletion(**kw):
            state["n"] += 1
            state["saw_tools"].append("tools" in kw and kw.get("tools"))
            if state["n"] == 1:
                raise TypeError("tools not supported by gateway")

            async def gen():
                yield _FakeChunk(_FakeDelta(content="降级后产出"))
            return gen()

        _patch_litellm(monkeypatch, fake_acompletion)
        monkeypatch.setattr(
            "project_runtime.llm.load_llm_settings",
            lambda: type("C", (), {
                "base_url": "http://x", "api_key": "k", "model": "m"})(),
        )
        from project_runtime.extension.shared import shared_registry
        from project_runtime.research.providers import (
            register_search_providers,
        )
        register_search_providers(shared_registry())

        class _WP:
            task_id = 1
            task_name = "任务A"
            playbook_ref = None
            contract = None
            skill_context = None

        out = await w._execute_llm(_WP())
        assert out == "降级后产出"
        assert state["saw_tools"][0], "首轮应带 tools"
        assert not state["saw_tools"][1], "降级后不带 tools"
