"""Plugin Bridge — wires plugin extensions into the D16 ExtensionRegistry.

WP-033 / SC01: on plugin start, each capability extension becomes a
PluginCapabilityProvider registered in the (single, unified) D16 registry;
on stop/disable/uninstall they are unregistered. Core modules keep seeing
only the Provider contract — they never learn where a provider lives.
"""

from __future__ import annotations

import logging

from project_runtime.extension.registry import (
    ExtensionRegistry,
)
from project_runtime.plugin.capability import (
    PluginCapabilityProvider,
    manifest_to_providers,
)
from project_runtime.plugin.rpc import PluginRPCClient

logger = logging.getLogger(__name__)


class PluginBridge:
    """Owns provider registration for running plugins.

    One instance per ExtensionRegistry facade. Lives in the runtime process;
    holds live RPC clients so providers can forward calls.
    """

    def __init__(self, registry: ExtensionRegistry):
        self._registry = registry
        # plugin_id → {provider.name: provider}
        self._by_plugin: dict[str, dict[str, PluginCapabilityProvider]] = {}

    def register_plugin(
        self,
        plugin_id: str,
        extensions: list[dict],
        permissions: list[str],
        client: PluginRPCClient,
        priority: int = 50,
    ) -> list[str]:
        """Create providers from manifest + bind live client + register."""
        providers = manifest_to_providers(plugin_id, extensions, permissions, priority)
        registered: dict[str, PluginCapabilityProvider] = {}
        for p in providers:
            p.bind_client(client)
            self._registry.register(p)
            registered[p.name] = p
        self._by_plugin[plugin_id] = registered
        names = list(registered.keys())
        if names:
            logger.info(
                "Plugin %s bridged %d capability provider(s): %s",
                plugin_id, len(names), names,
            )
        return names

    def unregister_plugin(self, plugin_id: str) -> list[str]:
        """Unregister all providers of a plugin (stop/disable/uninstall)."""
        registered = self._by_plugin.pop(plugin_id, {})
        for name, p in registered.items():
            try:
                self._registry.unregister(name)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.debug("unregister %s failed", name, exc_info=True)
            p.unbind_client()
        return list(registered.keys())

    def rebind_client(self, plugin_id: str, client: PluginRPCClient) -> None:
        """Re-bind a live client after plugin restart."""
        for p in self._by_plugin.get(plugin_id, {}).values():
            p.bind_client(client)

    def providers_of(self, plugin_id: str) -> list[PluginCapabilityProvider]:
        return list(self._by_plugin.get(plugin_id, {}).values())
