"""JSON-RPC 2.0 over stdio client (WP-032 固定实现 Transport).

Speaks the plugin RPC contract (04 规范):
    plugin.initialize / plugin.health / plugin.shutdown / extension.invoke

Each RPC is request/response with an id; responses are matched by id.
Process lifecycle is owned by PluginManager — this module only does IO.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

logger = logging.getLogger(__name__)

RPC_TIMEOUT_S = 20.0
INIT_TIMEOUT_S = 30.0


class PluginRPCError(Exception):
    pass


class PluginRPCClient:
    """One stdio pipe pair to one plugin process."""

    def __init__(self, process: asyncio.subprocess.Process):
        self._proc = process
        self._lock = asyncio.Lock()  # serialize request/response pairs

    @classmethod
    async def spawn(cls, cmd: list[str], cwd: str | None = None) -> "PluginRPCClient":
        """Start the plugin process with stdio pipes."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise PluginRPCError(f"Failed to spawn plugin process: {e}") from e
        return cls(proc)

    async def _request(
        self, method: str, params: dict | None = None, timeout: float = RPC_TIMEOUT_S
    ) -> dict:
        if self._proc.returncode is not None:
            raise PluginRPCError(
                f"Plugin process exited (code {self._proc.returncode})"
            )
        req_id = uuid.uuid4().hex[:8]
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(request, ensure_ascii=False) + "\n"

        async with self._lock:
            try:
                assert self._proc.stdin and self._proc.stdout
                self._proc.stdin.write(line.encode("utf-8"))
                await self._proc.stdin.drain()

                # Read lines until our id matches (skip notifications)
                while True:
                    raw = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=timeout
                    )
                    if not raw:
                        raise PluginRPCError("Plugin closed stdout unexpectedly")
                    try:
                        resp = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        logger.debug("plugin sent non-JSON line: %r", raw[:120])
                        continue
                    if resp.get("id") == req_id:
                        break
                    # not ours (notification/other) — keep reading
            except asyncio.TimeoutError as e:
                raise PluginRPCError(f"RPC {method} timed out after {timeout}s") from e
            except (ConnectionResetError, BrokenPipeError) as e:
                # Dead-process race: pipe breaks BEFORE returncode updates
                # (observed on Windows). All failure modes surface as
                # PluginRPCError so callers never see platform differences.
                raise PluginRPCError(
                    f"plugin process pipe broken (method={method})"
                ) from e

        if "error" in resp:
            err = resp["error"]
            raise PluginRPCError(
                f"RPC {method} failed: {err.get('message')} (code {err.get('code')})"
            )
        return resp.get("result") or {}

    # ── 04 规范基础方法 ─────────────────────────────────────────────────

    async def initialize(self, runtime_api: str) -> dict:
        return await self._request(
            "plugin.initialize",
            {"runtime_api_version": runtime_api},
            timeout=INIT_TIMEOUT_S,
        )

    async def health(self) -> dict:
        return await self._request("plugin.health")

    async def shutdown(self) -> dict:
        return await self._request("plugin.shutdown")

    async def invoke_extension(
        self, extension_id: str, payload: dict, timeout: float = RPC_TIMEOUT_S
    ) -> dict:
        return await self._request(
            "extension.invoke",
            {"extension_id": extension_id, "payload": payload},
            timeout=timeout,
        )

    async def terminate(self) -> None:
        """Kill the process if still alive. Never raises."""
        if self._proc.returncode is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
