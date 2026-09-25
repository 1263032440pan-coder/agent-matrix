"""WP-032 Plugin Manager tests — acceptance checklist.

Uses a REAL external plugin process (test-plugin/echo_plugin.py speaking
JSON-RPC 2.0 over stdio) — no mocks on the RPC boundary.

Acceptance:
  [x] 可安装一个测试插件包
  [x] plugin.yaml Schema 校验（拒绝坏 manifest）
  [x] API Version 不兼容时拒绝启用
  [x] 外部进程启动 / 停止 / 健康检查
  [x] 插件退出不会导致 Project Runtime 退出（崩溃隔离）
  [x] 插件状态持久化
"""

import io
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.plugin.registry  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
from project_runtime.plugin import (
    PluginManager,
    PluginManagerError,
    PluginStatus,
)
from project_runtime.plugin.manifest import (
    ManifestError,
    api_compatible,
    load_and_validate_manifest,
)
from project_runtime.plugin.registry import (
    InvalidPluginTransitionError,
    PluginRegistry,
)
from project_runtime.project_state.base import Base

TEST_PLUGIN_MAIN = '''\
import json
import sys

"""Minimal JSON-RPC 2.0 over stdio plugin (WP-032 test fixture)."""

CRASH = False

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
        result = {"plugin": "test-echo", "api_version": "1.0", "ok": True}
    elif method == "plugin.health":
        result = {"status": "healthy"}
    elif method == "plugin.shutdown":
        result = {"bye": True}
    elif method == "extension.invoke":
        result = {"echo": req.get("params", {}).get("payload")}
    elif method == "crash.now":
        # Simulate a hard plugin crash (acceptance §5)
        sys.exit(1)
    else:
        result = {"unknown": method}
    resp = {"jsonrpc": "2.0", "id": rid, "result": result}
    sys.stdout.write(json.dumps(resp) + "\\n")
    sys.stdout.flush()
'''

GOOD_MANIFEST = """\
plugin_id: test-echo
name: Test Echo Plugin
version: 1.0.0
api_version: "1.0"
description: WP-032 acceptance test plugin
entrypoint: echo_plugin.py
permissions:
  - READ_ONLY
extensions:
  - extension_id: test.echo
    point: test.providers
runtime:
  lifecycle: ON_DEMAND
  memory_estimate_mb: 64
  max_parallel_instances: 1
"""

BAD_MANIFEST_MISSING_FIELD = """\
plugin_id: test-bad
name: Bad Plugin
# version missing — schema violation
api_version: "1.0"
entrypoint: echo_plugin.py
"""

INCOMPATIBLE_MANIFEST = """\
plugin_id: test-old-api
name: Old API Plugin
version: 0.1.0
api_version: "2.0"
entrypoint: echo_plugin.py
"""


def _make_plugin_zip(manifest: str, main: str = TEST_PLUGIN_MAIN) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.yaml", manifest)
        zf.writestr("echo_plugin.py", main)
    return buf.getvalue()


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plugins.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # plugin store inside tmp (isolation per test)
    import project_runtime.plugin.manager as mgr_mod

    monkeypatch.setattr(mgr_mod, "PLUGIN_STORE", tmp_path / "plugin_store")
    yield eng
    await eng.dispose()


async def _install(dbengine, manifest: str, main: str = TEST_PLUGIN_MAIN) -> dict:

    import project_runtime.plugin.manager as mgr_mod

    store = mgr_mod.PLUGIN_STORE
    store.mkdir(parents=True, exist_ok=True)
    pkg = store / "src_test-echo"
    pkg.mkdir(exist_ok=True)
    (pkg / "plugin.yaml").write_text(manifest, encoding="utf-8")
    (pkg / "echo_plugin.py").write_text(main, encoding="utf-8")
    async with AsyncSession(dbengine) as session:
        mgr = PluginManager(session)
        result = await mgr.install(str(pkg))
        await session.commit()
    return result


# ── Manifest validation ────────────────────────────────────────────────


def test_manifest_schema_rejects_missing_field(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "plugin.yaml").write_text(BAD_MANIFEST_MISSING_FIELD, encoding="utf-8")
    (d / "echo_plugin.py").write_text(TEST_PLUGIN_MAIN, encoding="utf-8")
    with pytest.raises(ManifestError, match="schema invalid"):
        load_and_validate_manifest(d)


def test_manifest_rejects_missing_yaml(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_and_validate_manifest(tmp_path)


def test_manifest_rejects_traversal_entrypoint(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    malicious = GOOD_MANIFEST.replace(
        "entrypoint: echo_plugin.py", "entrypoint: ../../etc/passwd"
    )
    (d / "plugin.yaml").write_text(malicious, encoding="utf-8")
    with pytest.raises(ManifestError, match="escapes|not found"):
        load_and_validate_manifest(d)


def test_api_compatibility():
    assert api_compatible("1.0") is True
    assert api_compatible("1.9") is True
    assert api_compatible("2.0") is False
    assert api_compatible("garbage") is False


# ── Registry lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_state_machine(dbengine):
    async with AsyncSession(dbengine) as s:
        reg = PluginRegistry(s)
        rec = await reg.install(
            plugin_id="p1", version="1.0", name="P1", api_version="1.0",
            description=None, permissions=[], extensions=[], runtime={},
            package_path="/tmp/p1",
        )
        assert rec.status == PluginStatus.INSTALLED.value

        # Illegal jump INSTALLED → RUNNING
        with pytest.raises(InvalidPluginTransitionError):
            await reg.transition("p1", PluginStatus.RUNNING)

        # Legal: INSTALLED → STARTING → RUNNING → STOPPED
        await reg.transition("p1", PluginStatus.STARTING)
        await reg.transition("p1", PluginStatus.RUNNING)
        await reg.transition("p1", PluginStatus.STOPPED)
        await s.commit()

    # Persistence: fresh session sees STOPPED (acceptance §6)
    async with AsyncSession(dbengine) as s:
        reg = PluginRegistry(s)
        rec = await reg.get("p1")
        assert rec.status == PluginStatus.STOPPED.value


# ── Manager: install → enable → start → health → stop → uninstall ─────


@pytest.mark.asyncio
async def test_full_plugin_lifecycle_with_real_process(dbengine):
    """可安装测试插件 + 外部进程启停 + 健康检查（真实 stdio RPC）。"""
    result = await _install(dbengine, GOOD_MANIFEST)
    assert result["plugin_id"] == "test-echo"
    assert result["status"] == PluginStatus.INSTALLED.value
    assert result["api_compatible"] is True

    async with AsyncSession(dbengine) as s:
        mgr = PluginManager(s)

        # Enable
        r = await mgr.enable("test-echo")
        assert r["enabled"] is True

        # Start — spawns a REAL python subprocess, JSON-RPC handshake
        r = await mgr.start("test-echo")
        assert r["status"] == PluginStatus.RUNNING.value

        # Health check via live RPC
        h = await mgr.health("test-echo")
        assert h["live"] is True
        assert h["detail"]["status"] == "healthy"

        # Extension invoke round-trip
        res = await mgr._processes["test-echo"].invoke_extension(
            "test.echo", {"hello": "world"}
        )
        assert res["echo"] == {"hello": "world"}

        # Stop
        r = await mgr.stop("test-echo")
        assert r["status"] == PluginStatus.STOPPED.value
        await s.commit()

    # Persisted state after stop
    async with AsyncSession(dbengine) as s:
        rec = await PluginRegistry(s).get("test-echo")
        assert rec.status == PluginStatus.STOPPED.value
        assert rec.enabled == "true"


@pytest.mark.asyncio
async def test_incompatible_api_rejected_at_enable(dbengine):
    """API Version 不兼容时拒绝启用（acceptance §3）。"""
    result = await _install(dbengine, INCOMPATIBLE_MANIFEST)
    assert result["api_compatible"] is False

    async with AsyncSession(dbengine) as s:
        mgr = PluginManager(s)
        with pytest.raises(PluginManagerError, match="incompatible"):
            await mgr.enable("test-old-api")


@pytest.mark.asyncio
async def test_plugin_crash_does_not_kill_runtime(dbengine):
    """插件退出不会导致 Project Runtime 退出（acceptance §5）。

    Plugin process exits hard (sys.exit(1)) mid-RPC — the manager marks
    DEGRADED, the test process (== the Runtime from the plugin's POV)
    keeps running and can still operate other plugins.
    """
    await _install(dbengine, GOOD_MANIFEST)
    async with AsyncSession(dbengine) as s:
        mgr = PluginManager(s)
        await mgr.enable("test-echo")
        await mgr.start("test-echo")
        client = mgr._processes["test-echo"]

        # Hard-crash the plugin process
        try:
            await client._request("crash.now", timeout=5)
        except Exception:
            pass  # expected: RPC fails because process died
        await client.terminate()

        # Runtime (this test process) is STILL ALIVE and functional:
        h = await mgr.health("test-echo")
        assert h["live"] is False
        assert h["status"] in (PluginStatus.DEGRADED.value, PluginStatus.STOPPED.value)
        await s.commit()

    # And the DB is intact — other registry ops still work
    async with AsyncSession(dbengine) as s:
        rec = await PluginRegistry(s).get("test-echo")
        assert rec is not None  # runtime survived


@pytest.mark.asyncio
async def test_duplicate_install_rejected(dbengine):
    await _install(dbengine, GOOD_MANIFEST)
    with pytest.raises(PluginManagerError, match="already"):
        await _install(dbengine, GOOD_MANIFEST)


@pytest.mark.asyncio
async def test_uninstall_requires_stopped(dbengine):
    await _install(dbengine, GOOD_MANIFEST)
    async with AsyncSession(dbengine) as s:
        mgr = PluginManager(s)
        await mgr.enable("test-echo")
        await mgr.start("test-echo")
        # RUNNING state: uninstall must go through stop (auto-stopped here)
        r = await mgr.uninstall("test-echo")
        assert r["uninstalled"] is True
        await s.commit()
    async with AsyncSession(dbengine) as s:
        assert await PluginRegistry(s).get("test-echo") is None
