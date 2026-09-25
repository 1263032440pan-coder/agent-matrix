"""Plugin package (WP-032): manifest / registry / manager / stdio RPC."""

from project_runtime.plugin.bridge import PluginBridge
from project_runtime.plugin.capability import (
    PluginCapabilityProvider,
    manifest_to_providers,
)
from project_runtime.plugin.manager import (
    PluginManager,
    PluginManagerError,
    PluginStatus,
)
from project_runtime.plugin.manifest import (
    RUNTIME_PLUGIN_API,
    ManifestError,
    api_compatible,
)
from project_runtime.plugin.registry import (
    PluginRecord,
    PluginRegistry,
    PluginRegistryError,
)

__all__ = [
    "ManifestError",
    "PluginBridge",
    "PluginCapabilityProvider",
    "PluginManager",
    "PluginManagerError",
    "PluginRecord",
    "PluginRegistry",
    "PluginRegistryError",
    "PluginStatus",
    "RUNTIME_PLUGIN_API",
    "api_compatible",
    "manifest_to_providers",
]
