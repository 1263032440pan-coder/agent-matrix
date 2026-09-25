"""WP-033 Capability Extension Framework tests — acceptance checklist.

SC01 bridge verified end-to-end with a REAL plugin process:
  plugin (stdio) → PluginCapabilityProvider → D16 ExtensionRegistry → invoke

Acceptance:
  [x] Plugin 可注册 Capability Extension
  [x] Provider 可按 capability 查询
  [x] Provider 异常有统一错误
  [x] 支持优先级与 enable/disable
  [x] 核心模块只依赖 Provider Contract（invoke 走 D16 门面，核心不知插件存在）
"""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.plugin.registry  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
from project_runtime.extension.registry import (
    CapabilityType,
    ExtensionError,
    ExtensionRegistry,
    ExtensionUnavailableError,
    Permission,
)
from project_runtime.plugin import PluginBridge, PluginManager
from project_runtime.plugin.capability import manifest_to_providers
from project_runtime.project_state.base import Base

# A plugin exposing a TOOL capability extension via stdio JSON-RPC
CAPABILITY_PLUGIN_MAIN = '''\
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = req.get("method", "")
    rid = req.get("id")
    if method == "plugin.initialize":
        result = {"ok": True}
    elif method == "plugin.health":
        result = {"status": "healthy"}
    elif method == "plugin.shutdown":
        result = {"bye": True}
    elif method == "extension.invoke":
        p = req.get("params", {})
        if p.get("extension_id") == "echo.tool":
            result = {"result": {"tool_output": p.get("payload", {}).get("x")}}
        else:
            result = {"error": "unknown extension"}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
    sys.stdout.flush()
'''

CAPABILITY_MANIFEST = """\
plugin_id: cap-tool-plugin
name: Capability Tool Plugin
version: 1.0.0
api_version: "1.0"
description: WP-033 test plugin exposing a TOOL capability
entrypoint: cap_plugin.py
permissions:
  - ENRICH
extensions:
  - extension_id: echo.tool
    point: tool.providers
  - extension_id: some.runtime.hook
    point: worker.after_execute
"""

import project_runtime.plugin.manager as mgr_mod  # noqa: E402


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cap.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(mgr_mod, "PLUGIN_STORE", tmp_path / "plugin_store")
    yield eng
    await eng.dispose()


async def _install_and_start(dbengine, registry: ExtensionRegistry) -> PluginManager:
    store = mgr_mod.PLUGIN_STORE
    store.mkdir(parents=True, exist_ok=True)
    pkg = store / "src_cap"
    pkg.mkdir(exist_ok=True)
    (pkg / "plugin.yaml").write_text(CAPABILITY_MANIFEST, encoding="utf-8")
    (pkg / "cap_plugin.py").write_text(CAPABILITY_PLUGIN_MAIN, encoding="utf-8")

    bridge = PluginBridge(registry)
    async with AsyncSession(dbengine) as s:
        mgr = PluginManager(s, bridge=bridge)
        await mgr.install(str(pkg))
        await mgr.enable("cap-tool-plugin")
        r = await mgr.start("cap-tool-plugin")
        await s.commit()
        assert r["status"] == "RUNNING"
        return mgr


# ── Unit: manifest → providers mapping ────────────────────────────────


def test_manifest_to_providers_maps_capability_points():
    exts = [
        {"extension_id": "a", "point": "tool.providers"},
        {"extension_id": "b", "point": "retrieval.providers"},
        {"extension_id": "c", "point": "worker.after_execute"},  # runtime point → skip
    ]
    providers = manifest_to_providers("p1", exts, ["ENRICH"])
    assert len(providers) == 2  # runtime-extension point not a capability
    caps = {p.capability for p in providers}
    assert caps == {CapabilityType.TOOL, CapabilityType.RETRIEVAL}
    assert providers[0].permission == Permission.ENRICH
    assert providers[0].name == "p1:a"


def test_manifest_to_providers_permission_fallback():
    providers = manifest_to_providers("p2", [{"extension_id": "x", "point": "llm.providers"}], [])
    assert providers[0].permission == Permission.READ_ONLY  # default


# ── End-to-end: plugin process → D16 facade ───────────────────────────


@pytest.mark.asyncio
async def test_plugin_registers_capability_via_bridge(dbengine):
    """Plugin 可注册 Capability Extension（真实进程），核心经 D16 门面调用。"""
    registry = ExtensionRegistry()
    mgr = await _install_and_start(dbengine, registry)

    # Provider visible in the unified registry (acceptance §1)
    tools = registry.list_providers(CapabilityType.TOOL)
    assert len(tools) == 1
    assert tools[0].name == "cap-tool-plugin:echo.tool"
    assert tools[0].capability == CapabilityType.TOOL

    # Core module calls THROUGH the facade (acceptance §5) — the call
    # crosses the process boundary and returns the plugin's result
    result = await registry.invoke(CapabilityType.TOOL, {"x": "hello"})
    assert result == {"tool_output": "hello"}

    await mgr.stop("cap-tool-plugin")


@pytest.mark.asyncio
async def test_provider_query_by_capability(dbengine):
    """Provider 可按 capability 查询——只返回该能力域的 provider。"""
    registry = ExtensionRegistry()
    mgr = await _install_and_start(dbengine, registry)

    assert len(registry.list_providers(CapabilityType.TOOL)) == 1
    assert len(registry.list_providers(CapabilityType.RETRIEVAL)) == 0
    assert len(registry.list_providers()) == 1

    await mgr.stop("cap-tool-plugin")


@pytest.mark.asyncio
async def test_unified_error_when_no_provider(dbengine):
    """Provider 异常有统一错误——无 provider / 插件报错都走 ExtensionError 家族。"""
    registry = ExtensionRegistry()

    # No provider at all → ExtensionUnavailableError (D16 unified error)
    with pytest.raises(ExtensionUnavailableError):
        await registry.invoke(CapabilityType.RETRIEVAL, {})

    mgr = await _install_and_start(dbengine, registry)
    # Unbound provider (plugin not started from this provider's view)
    providers = registry.list_providers(CapabilityType.TOOL)
    providers[0].provider.unbind_client()
    with pytest.raises(ExtensionError, match="no live process"):
        await providers[0].provider.invoke({"x": 1})

    await mgr.stop("cap-tool-plugin")


@pytest.mark.asyncio
async def test_priority_and_enable_disable(dbengine):
    """支持优先级与 enable/disable——D16 门面控制插件来源的 provider。"""
    registry = ExtensionRegistry()
    mgr = await _install_and_start(dbengine, registry)

    # In-process provider with HIGHER priority coexisting with the plugin one
    class LocalProvider:
        name = "local.tool"
        capability = CapabilityType.TOOL
        permission = Permission.READ_ONLY
        priority = 100  # beats plugin's default 50
        enabled = True

        async def health(self):
            return True

        async def invoke(self, payload):
            return {"from": "local"}

    registry.register(LocalProvider())

    # Priority: local wins
    assert (await registry.invoke(CapabilityType.TOOL, {}))["from"] == "local"

    # Disable local → plugin provider serves
    registry.set_enabled("local.tool", False)
    assert (await registry.invoke(CapabilityType.TOOL, {"x": 1})) == {"tool_output": 1}

    # Disable plugin provider too → no provider
    registry.set_enabled("cap-tool-plugin:echo.tool", False)
    with pytest.raises(ExtensionUnavailableError):
        await registry.invoke(CapabilityType.TOOL, {})

    await mgr.stop("cap-tool-plugin")


@pytest.mark.asyncio
async def test_stop_unregisters_providers(dbengine):
    """Stop 插件后 provider 从门面摘除（不留幽灵 provider）。"""
    registry = ExtensionRegistry()
    mgr = await _install_and_start(dbengine, registry)
    assert len(registry.list_providers(CapabilityType.TOOL)) == 1

    await mgr.stop("cap-tool-plugin")
    assert len(registry.list_providers(CapabilityType.TOOL)) == 0
    with pytest.raises(ExtensionUnavailableError):
        await registry.invoke(CapabilityType.TOOL, {})


@pytest.mark.asyncio
async def test_crashed_plugin_provider_unhealthy(dbengine):
    """插件进程死 → provider health=False、调用统一报错，Runtime 存活。"""
    registry = ExtensionRegistry()
    mgr = await _install_and_start(dbengine, registry)

    client = mgr._processes["cap-tool-plugin"]
    await client.terminate()  # hard kill

    providers = registry.list_providers(CapabilityType.TOOL)
    assert await providers[0].provider.health() is False
    # WP-033 统一错误: RPC 失败（死进程/断管）包装为 ExtensionError
    with pytest.raises(ExtensionError, match="RPC failed"):
        await providers[0].provider.invoke({"x": 1})

    await mgr.stop("cap-tool-plugin")
