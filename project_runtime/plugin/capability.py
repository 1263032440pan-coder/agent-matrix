"""Plugin Capability Provider — WP-033 / SC01 bridge (WP-032 复验衔接点).

Bridges WP-032 external plugins into the D16 ExtensionRegistry:

    Plugin (stdio JSON-RPC)  ──PluginCapabilityProvider──▶  D16 Provider Contract

Design (SC01): D16 ExtensionRegistry stays the SINGLE unified facade — core
modules keep depending only on the Provider contract (WP-033 要求). Plugins
are one SOURCE of providers (layered, not merged): the manifest's
ExtensionDecl.point maps to CapabilityType, and every call is forwarded to
the plugin process via extension.invoke RPC.
"""

from __future__ import annotations

import logging
from typing import Any

from project_runtime.extension.registry import (
    CapabilityType,
    Permission,
)
from project_runtime.plugin.rpc import PluginRPCClient, PluginRPCError

logger = logging.getLogger(__name__)

# manifest extension point → D16 CapabilityType (SC01 mapping)
POINT_TO_CAPABILITY: dict[str, CapabilityType] = {
    "tool.providers": CapabilityType.TOOL,
    "retrieval.providers": CapabilityType.RETRIEVAL,
    "research.providers": CapabilityType.RESEARCH,
    "worker.providers": CapabilityType.WORKER_RUNTIME,
    "worker_runtime.providers": CapabilityType.WORKER_RUNTIME,
    "knowledge.providers": CapabilityType.KNOWLEDGE,
    "llm.providers": CapabilityType.LLM,
    "artifact.processors": CapabilityType.ARTIFACT_PROCESSOR,
}

# manifest permission string → D16 Permission
PERMISSION_MAP: dict[str, Permission] = {
    "READ_ONLY": Permission.READ_ONLY,
    "ENRICH": Permission.ENRICH,
    "VALIDATE": Permission.VALIDATE,
    "CONTROL": Permission.CONTROL,
}


class PluginCapabilityProvider:
    """A D16 Provider backed by one extension of one external plugin.

    Implements the Provider protocol: name / capability / permission /
    priority / enabled + health() / invoke(). All invocations cross the
    process boundary as `extension.invoke` JSON-RPC calls.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        extension_id: str,
        capability: CapabilityType,
        permission: Permission,
        priority: int = 50,
    ):
        self.name = f"{plugin_id}:{extension_id}"
        self.capability = capability
        self.permission = permission
        self.priority = priority
        self.enabled = True
        self._plugin_id = plugin_id
        self._extension_id = extension_id
        self._client: PluginRPCClient | None = None

    # Process wiring (managed by the plugin bridge, not the provider itself)
    def bind_client(self, client: PluginRPCClient) -> None:
        self._client = client

    def unbind_client(self) -> None:
        self._client = None

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    async def health(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.health()
            return True
        except PluginRPCError:
            return False

    async def invoke(self, payload: dict[str, Any]) -> Any:
        from project_runtime.extension.registry import ExtensionError

        if self._client is None:
            raise ExtensionError(
                f"Plugin provider {self.name} has no live process "
                f"(plugin not started)"
            )
        # Forward across the process boundary (WP-032 transport).
        # WP-033 统一错误: RPC 层失败（超时/管道断/进程死）一律包装为
        # ExtensionError —— 核心模块只见统一错误家族。
        try:
            result = await self._client.invoke_extension(
                self._extension_id, payload
            )
        except PluginRPCError as e:
            raise ExtensionError(
                f"Plugin provider {self.name} RPC failed: {e}"
            ) from e
        # Plugin contract: {"result": ...} or {"error": ...}
        if isinstance(result, dict) and "error" in result:
            from project_runtime.extension.registry import ExtensionError

            raise ExtensionError(
                f"Plugin provider {self.name} returned error: {result['error']}"
            )
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result


def manifest_to_providers(
    plugin_id: str,
    extensions: list[dict],
    permissions: list[str],
    priority: int = 50,
) -> list[PluginCapabilityProvider]:
    """Build providers from a plugin manifest's declared extensions.

    Extensions whose point is not in the capability map are skipped
    (logged) — they may target Runtime Extension points (WP-034 scope).
    """
    perm = Permission.READ_ONLY
    for p in permissions:
        perm = PERMISSION_MAP.get(p, perm)

    providers: list[PluginCapabilityProvider] = []
    for ext in extensions:
        point = ext.get("point", "")
        cap = POINT_TO_CAPABILITY.get(point)
        if cap is None:
            logger.debug(
                "Plugin %s extension %s targets non-capability point %s (skip)",
                plugin_id, ext.get("extension_id"), point,
            )
            continue
        providers.append(
            PluginCapabilityProvider(
                plugin_id=plugin_id,
                extension_id=ext.get("extension_id", "default"),
                capability=cap,
                permission=perm,
                priority=priority,
            )
        )
    return providers
