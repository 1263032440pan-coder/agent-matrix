"""MCP 桥接（20号规范 §10.3 批次 3——外部 MCP 服务器 → TOOL 插件）.

opencode 主扩展路径的 Demo 版:
  - MCP 客户端: stdio（本地命令）与 HTTP/SSE（远程端点）两形态
  - 桥接方向: MCP server 的 tools → 注册为本项目 TOOL Provider
    （描述即调用策略——与19号工具循环无缝: 模型自决调用）
  - 纪律: MCP 是外部能力面——桥接器只透传 name/description/inputSchema,
    不做语义加工; 超时/失败按 ProviderError 降级（能力缺席不阻断任务）
  - Demo V0.1 简化: 无 MCP 会话保活（每次调用冷启动 stdio /
    HTTP 无状态直发）——真实 MCP 客户端生命周期后置

形态参照 19 号 ExaProvider（mcp.exa.ai keyless HTTP）——本项目已有
MCP-over-HTTP 的实战先例，桥接器是它的通用化。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MCP_CALL_TIMEOUT_S = 60
MCP_LIST_TIMEOUT_S = 20


class McpBridgeError(Exception):
    """MCP 桥接失败（连接/协议/超时——统一降级语义）。"""


@dataclass
class McpServerConfig:
    """一个外部 MCP 服务器的连接描述。

    - transport="stdio": command + args（本地进程，JSON-RPC over stdin/stdout）
    - transport="http":  url（MCP over HTTP POST——Exa 同款）
    """

    name: str
    transport: str = "http"          # stdio | http
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


class McpBridge:
    """把 MCP server 的 tools 桥接为本项目 TOOL 能力 Provider。

    用法（组合根/注册期）:
        bridge = McpBridge()
        specs = await bridge.list_tools(server_cfg)
        for spec in specs:
            register_as_tool(spec, bridge, server_cfg)  # → shared_registry
    """

    async def list_tools(
        self, cfg: McpServerConfig
    ) -> list[dict]:
        """列 MCP server 工具（tools/list）→ [{name, description, schema}]。"""
        raw = await self._rpc(cfg, "tools/list", {})
        tools = (raw or {}).get("tools") or []
        out = []
        for t in tools:
            out.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema") or {},
            })
        return [t for t in out if t["name"]]

    async def call_tool(
        self, cfg: McpServerConfig, tool_name: str, arguments: dict
    ) -> str:
        """调 MCP 工具（tools/call）→ 文本结果（喂回工具循环）。"""
        raw = await self._rpc(
            cfg, "tools/call", {"name": tool_name, "arguments": arguments}
        )
        # MCP 返回 content 数组——拼 text 块
        content = (raw or {}).get("content") or []
        texts = [
            c.get("text", "") for c in content if c.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t)

    # ── 传输层 ───────────────────────────────────────────────────

    async def _rpc(
        self, cfg: McpServerConfig, method: str, params: dict
    ) -> dict:
        if cfg.transport == "stdio":
            return await self._rpc_stdio(cfg, method, params)
        return await self._rpc_http(cfg, method, params)

    async def _rpc_http(
        self, cfg: McpServerConfig, method: str, params: dict
    ) -> dict:
        """MCP over HTTP POST（JSON-RPC 2.0 单发单收——无会话 Demo 版）。"""
        import urllib.request

        if not cfg.url:
            raise McpBridgeError(f"MCP server {cfg.name}: 无 url")
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        }).encode("utf-8")
        req = urllib.request.Request(
            cfg.url, data=body,
            headers={"Content-Type": "application/json", **cfg.headers},
        )
        try:
            with urllib.request.urlopen(  # noqa: S310
                req, timeout=MCP_CALL_TIMEOUT_S
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise McpBridgeError(
                f"MCP {cfg.name} {method} 失败: {e}"
            ) from e
        if "error" in data:
            raise McpBridgeError(
                f"MCP {cfg.name} {method} 协议错误: {data['error']}"
            )
        return data.get("result") or {}

    async def _rpc_stdio(
        self, cfg: McpServerConfig, method: str, params: dict
    ) -> dict:
        """stdio 传输: 起子进程、写一行 JSON-RPC、读一行结果、杀进程。

        Demo 简化: 单请求生命周期（list 与 call 各自冷启动）——
        initialize 握手省略（多数 server 容忍直发 tools/list）。
        """
        if not cfg.command:
            raise McpBridgeError(f"MCP server {cfg.name}: 无 command")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                cfg.command, *cfg.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            line = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            })
            assert proc.stdin and proc.stdout
            proc.stdin.write(line.encode("utf-8") + b"\n")
            await proc.stdin.drain()
            # 读到含 "id" 的行为止（server 启动横幅可能先出若干行）
            deadline = asyncio.get_event_loop().time() + MCP_CALL_TIMEOUT_S
            while True:
                remain = deadline - asyncio.get_event_loop().time()
                if remain <= 0:
                    raise McpBridgeError(
                        f"MCP stdio {cfg.name} {method} 超时"
                    )
                raw_line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=remain
                )
                if not raw_line:
                    raise McpBridgeError(
                        f"MCP stdio {cfg.name} {method}: 进程提前退出"
                    )
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text or "jsonrpc" not in text:
                    continue
                data = json.loads(text)
                if "error" in data:
                    raise McpBridgeError(
                        f"MCP {cfg.name} {method}: {data['error']}"
                    )
                return data.get("result") or {}
        except McpBridgeError:
            raise
        except Exception as e:  # noqa: BLE001
            raise McpBridgeError(
                f"MCP stdio {cfg.name} {method} 失败: {e}"
            ) from e
        finally:
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass


def mcp_tool_name(server: str, tool: str) -> str:
    """桥接工具命名: mcp_{server}_{tool}（防跨 server 重名）。"""
    return f"mcp_{server}_{tool}"


def register_mcp_server(
    cfg: McpServerConfig,
    specs: list[dict],
    registry=None,
    bridge: McpBridge | None = None,
) -> int:
    """把 list_tools 结果注册为 TOOL 能力 Provider（对齐 D16 协议）。

    registry=None → shared_registry（组合根默认）。
    返回注册数。
    """
    if registry is None:
        from project_runtime.extension.shared import shared_registry
        registry = shared_registry
    b = bridge or McpBridge()
    count = 0
    for spec in specs:
        if not spec.get("name"):
            continue
        registry.register(_McpToolProvider(cfg, spec, b))
        count += 1
    return count


class _McpToolProvider:
    """单个 MCP 工具的 Provider（D16 协议——invoke 异步原生）。

    name = mcp_{server}_{tool}（防跨 server 重名; 与批次 1
    tool:{name} 前缀并存——worker 工具循环按 description 透出）。
    """

    def __init__(
        self, cfg: McpServerConfig, spec: dict, bridge: McpBridge
    ):
        from project_runtime.plugin.capability import CapabilityType

        self.name = mcp_tool_name(cfg.name, spec["name"])
        self.capability = CapabilityType.TOOL
        self.permission = "READ_ONLY"
        self.priority = 100
        self.enabled = True
        self._cfg = cfg
        self._spec = spec
        self._bridge = bridge
        self.description = (
            f"[MCP:{cfg.name}] {spec.get('description') or spec['name']}"
        )

    def tool_schema(self) -> dict:
        """工具循环描述（name/description/parameters——模型自决依据）。"""
        return {
            "name": self.name,
            "description": self._spec.get("description") or self._spec["name"],
            "parameters": self._spec.get("inputSchema") or {
                "type": "object", "properties": {},
            },
        }

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        try:
            result = await self._bridge.call_tool(
                self._cfg, self._spec["name"], payload or {}
            )
            return {"ok": True, "result": result}
        except McpBridgeError as e:
            # 降级语义（19号同款）: 能力缺席不炸任务
            return {"ok": False, "error": str(e)}
