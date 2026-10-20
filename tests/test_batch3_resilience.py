"""20号规范批次 3 测试: 成本账本 + 失败传播/快照 + MCP 桥接（全离线）.

覆盖:
  [x] 成本记账: usage 累计/调用计数/预算 0=不限/超预算抛错/汇总查询
  [x] usage 提取（LiteLLM 响应形态/缺省 0）
  [x] 失败传播三段: 无任务→wait / 有备胎→redispatch / 无备胎→block
  [x] Agent 会话快照: checkpoint 落库 + JSON 文件 + 接班说明含关键信息
  [x] MCP 桥接: list_tools 解析 / call_tool 文本拼接 / HTTP 失败降级 /
      协议 error 拒绝 / stdio 进程往返 / 命名防冲 / 注册进 registry
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode
from project_runtime.project_state.models import Project
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentSource,
    AgentStatus,
)
from project_runtime.worker.cost_ledger import (
    BudgetExceededError,
    CostEntry,
    CostLedger,
)
from project_runtime.worker.mcp_bridge import (
    McpBridge,
    McpBridgeError,
    McpServerConfig,
    mcp_tool_name,
    register_mcp_server,
)
from project_runtime.worker.resilience import (
    FailurePropagator,
    snapshot_agent_session,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/b3.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="b3", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


_AGENT_SEQ = [0]


async def _mk_agent(s, pid, *, status=AgentStatus.IDLE, budget=0,
                    task_id=None) -> AgentInstance:
    _AGENT_SEQ[0] += 1
    d = AgentDefinition(agent_id=f"a-{pid}-{_AGENT_SEQ[0]}",
                        name="A", source=AgentSource.BUILT_IN)
    s.add(d)
    await s.flush()
    inst = AgentInstance(
        definition_id=d.id, project_id=pid, status=status,
        token_budget=budget, current_task_id=task_id,
    )
    s.add(inst)
    await s.flush()
    return inst


# ── 成本账本 ───────────────────────────────────────────────────────


class TestCostLedger:
    @pytest.mark.asyncio
    async def test_record_accumulates(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            inst = await _mk_agent(s, pid)
            ledger = CostLedger(s)
            used = await ledger.record(CostEntry(
                inst.id, tokens_in=1000, tokens_out=500))
            assert used == 1500
            used = await ledger.record(CostEntry(
                inst.id, tokens_in=200, tokens_out=100))
            assert used == 1800
            await s.commit()
        async with AsyncSession(eng) as s:
            row = (await s.execute(
                select(AgentInstance))).scalars().first()
            assert row.token_used == 1800
            assert row.call_count == 2

    @pytest.mark.asyncio
    async def test_budget_zero_unlimited(self, dbengine):
        """预算 0 = 不限（默认）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            inst = await _mk_agent(s, pid, budget=0)
            ledger = CostLedger(s)
            used = await ledger.record(CostEntry(inst.id, tokens_in=99999))
            assert used == 99999  # 不抛

    @pytest.mark.asyncio
    async def test_budget_exceeded_raises(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            inst = await _mk_agent(s, pid, budget=1000)
            ledger = CostLedger(s)
            await ledger.record(CostEntry(inst.id, tokens_in=600))
            with pytest.raises(BudgetExceededError, match="预算耗尽"):
                await ledger.record(CostEntry(inst.id, tokens_in=600))
            await s.commit()

    @pytest.mark.asyncio
    async def test_summary(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            a1 = await _mk_agent(s, pid, budget=100)
            a2 = await _mk_agent(s, pid, budget=0)
            ledger = CostLedger(s)
            # a1 超预算: 记账成功（统计先落）+ 抛预算异常（调用方降级）
            with pytest.raises(BudgetExceededError):
                await ledger.record(CostEntry(a1.id, tokens_in=150))
            await ledger.record(CostEntry(a2.id, tokens_in=50))
            rows = await ledger.summary(pid)
            assert len(rows) == 2
            over = [r for r in rows if r["over_budget"]]
            assert len(over) == 1 and over[0]["token_used"] == 150

    def test_usage_extraction(self):
        resp = {"usage": {"prompt_tokens": 12, "completion_tokens": 34}}
        assert CostLedger.usage_from_response(resp) == (12, 34)
        assert CostLedger.usage_from_response({}) == (0, 0)
        assert CostLedger.usage_from_response(
            {"usage": None}) == (0, 0)


# ── 失败传播 + 会话快照 ────────────────────────────────────────────


class TestFailurePropagation:
    @pytest.mark.asyncio
    async def test_no_task_waits(self, dbengine):
        """无任务在身——等自愈。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            inst = await _mk_agent(s, pid, status=AgentStatus.DEGRADED)
            d = FailurePropagator(s)
            r = await d.on_agent_degraded(pid, inst.id, None)
            assert r["action"] == "wait"

    @pytest.mark.asyncio
    async def test_backup_redispatch(self, dbengine):
        """有 IDLE 备胎 → 任务转派。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            busy = await _mk_agent(
                s, pid, status=AgentStatus.DEGRADED, task_id=77)
            backup = await _mk_agent(s, pid, status=AgentStatus.IDLE)
            d = FailurePropagator(s)
            r = await d.on_agent_degraded(pid, busy.id, 77)
            assert r["action"] == "redispatch"
            assert r["backup_instance_id"] == backup.id
            assert backup.current_task_id == 77
            await s.commit()

    @pytest.mark.asyncio
    async def test_no_backup_blocks(self, dbengine):
        """无备胎 → block 升级（绝不静默丢任务）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            busy = await _mk_agent(
                s, pid, status=AgentStatus.DEGRADED, task_id=88)
            d = FailurePropagator(s)
            r = await d.on_agent_degraded(pid, busy.id, 88)
            assert r["action"] == "block"
            assert r["backup_instance_id"] is None

    @pytest.mark.asyncio
    async def test_snapshot(self, dbengine, tmp_path, monkeypatch):
        """DEGRADED 快照: checkpoint 行 + JSON 文件 + 接班说明。"""
        eng, pid = dbengine
        monkeypatch.chdir(tmp_path)  # data/checkpoints 落临时区
        async with AsyncSession(eng) as s:
            inst = await _mk_agent(
                s, pid, status=AgentStatus.DEGRADED, task_id=42)
            inst.session_ref = "sess-xyz"
            inst.context_tokens = 12000
            inst.context_threshold = 112000
            inst.token_used = 5000
            await s.flush()
            cp_id = await snapshot_agent_session(s, inst.id, "DEGRADED")
            await s.commit()
        assert cp_id is not None
        from project_runtime.checkpoint.models import CheckpointRecord

        async with AsyncSession(eng) as s:
            cp = await s.get(CheckpointRecord, cp_id)
            assert cp.trigger == "AGENT_DEGRADED"
            assert "42" in (cp.summary or "")        # 在办任务
            assert "12000" in (cp.summary or "")     # 上下文压力
        f = Path(cp.path)
        assert f.exists()
        payload = json.loads(f.read_text(encoding="utf-8"))
        assert payload["session_ref"] == "sess-xyz"
        assert payload["current_task_id"] == 42
        assert payload["reason"] == "DEGRADED"


# ── MCP 桥接 ───────────────────────────────────────────────────────


class _FakeHttpTransport:
    """HTTP 传输桩——list/call 两方法。"""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, cfg, method, params):
        self.calls.append((method, params))
        return self.result


class TestMcpBridge:
    @pytest.mark.asyncio
    async def test_list_tools_parses(self, monkeypatch):
        b = McpBridge()
        fake = _FakeHttpTransport({"tools": [
            {"name": "search", "description": "搜索", "inputSchema": {"x": 1}},
            {"description": "无名工具应被滤除"},
        ]})
        monkeypatch.setattr(b, "_rpc_http", fake)
        cfg = McpServerConfig(name="exa", url="https://x/mcp")
        tools = await b.list_tools(cfg)
        assert len(tools) == 1
        assert tools[0]["name"] == "search"
        assert tools[0]["inputSchema"] == {"x": 1}
        assert fake.calls[0][0] == "tools/list"

    @pytest.mark.asyncio
    async def test_call_tool_joins_text(self, monkeypatch):
        b = McpBridge()
        fake = _FakeHttpTransport({"content": [
            {"type": "text", "text": "结果A"},
            {"type": "image", "data": "zzz"},          # 非 text 忽略
            {"type": "text", "text": "结果B"},
        ]})
        monkeypatch.setattr(b, "_rpc_http", fake)
        cfg = McpServerConfig(name="exa", url="https://x/mcp")
        out = await b.call_tool(cfg, "search", {"q": "vue"})
        assert out == "结果A\n结果B"
        method, params = fake.calls[0]
        assert method == "tools/call"
        assert params == {"name": "search", "arguments": {"q": "vue"}}

    @pytest.mark.asyncio
    async def test_http_error_degrades(self):
        """HTTP 失败 → McpBridgeError（降级语义——不裸抛网络异常）。"""
        b = McpBridge()
        cfg = McpServerConfig(name="dead", url="http://127.0.0.1:1/mcp")
        with pytest.raises(McpBridgeError, match="dead"):
            await b.call_tool(cfg, "search", {})

    @pytest.mark.asyncio
    async def test_protocol_error_rejected(self, monkeypatch):
        """响应体带 error 键 → 协议错误拒绝（走真实 _rpc_http 分支）。"""
        import io
        import urllib.request

        class _Resp:
            def __init__(self, body: bytes):
                self._buf = io.BytesIO(body)

            def read(self) -> bytes:
                return self._buf.read()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1,
             "error": {"code": -32000, "message": "nope"}}
        ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return _Resp(body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        b = McpBridge()
        cfg = McpServerConfig(name="x", url="https://x/mcp")
        with pytest.raises(McpBridgeError, match="协议错误"):
            await b.list_tools(cfg)

    @pytest.mark.asyncio
    async def test_stdio_roundtrip(self):
        """stdio 传输: 真子进程（python -c 模拟 MCP server）。"""
        import sys

        server_code = (
            "import sys, json\n"
            "line = sys.stdin.readline()\n"
            "req = json.loads(line)\n"
            "print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': "
            "{'content': [{'type': 'text', 'text': 'stdio ok'}]}}))\n"
        )
        cfg = McpServerConfig(
            name="local", transport="stdio",
            command=sys.executable, args=["-c", server_code],
        )
        b = McpBridge()
        out = await b.call_tool(cfg, "echo", {})
        assert out == "stdio ok"

    @pytest.mark.asyncio
    async def test_stdio_timeout(self):
        """子进程不回应 → 超时 McpBridgeError（挂起进程被杀）。"""
        import sys

        server_code = "import time; time.sleep(60)"
        cfg = McpServerConfig(
            name="hang", transport="stdio",
            command=sys.executable, args=["-c", server_code],
        )
        b = McpBridge()
        import project_runtime.worker.mcp_bridge as mb

        old = mb.MCP_CALL_TIMEOUT_S
        mb.MCP_CALL_TIMEOUT_S = 2
        try:
            with pytest.raises(McpBridgeError, match="超时|退出|失败"):
                await b.call_tool(cfg, "echo", {})
        finally:
            mb.MCP_CALL_TIMEOUT_S = old

    def test_tool_name_prefixing(self):
        assert mcp_tool_name("exa", "search") == "mcp_exa_search"

    def test_register_into_registry(self):
        """注册面: MCP 工具 → ExtensionRegistry（D16 Provider 协议）。"""
        from project_runtime.extension.registry import ExtensionRegistry

        reg = ExtensionRegistry()
        specs = [
            {"name": "search", "description": "搜索",
             "inputSchema": {"type": "object", "properties": {}}},
        ]
        cfg = McpServerConfig(name="exa", url="https://x")
        n = register_mcp_server(cfg, specs, registry=reg)
        assert n == 1
        info = reg._providers[mcp_tool_name("exa", "search")]
        assert info.capability.value == "TOOL"
        provider = info.provider
        schema = provider.tool_schema()
        assert schema["name"] == "mcp_exa_search"

    @pytest.mark.asyncio
    async def test_provider_invoke_ok_and_fail(self, monkeypatch):
        """Provider.invoke: 正常/失败两路（失败返回 ok=False 不抛）。"""
        from project_runtime.extension.registry import ExtensionRegistry

        reg = ExtensionRegistry()
        cfg = McpServerConfig(name="exa", url="https://x")
        provider = None

        class _Stub(McpBridge):
            async def call_tool(self, cfg_, name, args):
                if args.get("boom"):
                    raise McpBridgeError("gateway 502")
                return "好的结果"

        register_mcp_server(
            cfg, [{"name": "search", "description": "d"}],
            registry=reg, bridge=_Stub(),
        )
        provider = reg._providers["mcp_exa_search"].provider
        ok = await provider.invoke({"q": "vue"})
        assert ok == {"ok": True, "result": "好的结果"}
        fail = await provider.invoke({"boom": 1})
        assert fail["ok"] is False
        assert "502" in fail["error"]
