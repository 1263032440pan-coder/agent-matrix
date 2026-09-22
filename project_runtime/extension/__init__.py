"""Extension module public API."""

from project_runtime.extension.registry import (
    CapabilityType,
    ExtensionError,
    ExtensionRegistry,
    ExtensionUnavailableError,
    Permission,
    Provider,
    ProviderInfo,
)

__all__ = [
    "ExtensionRegistry",
    "CapabilityType",
    "Permission",
    "Provider",
    "ProviderInfo",
    "ExtensionError",
    "ExtensionUnavailableError",
]
