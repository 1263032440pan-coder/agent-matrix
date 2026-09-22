"""ExtensionRegistry — minimal in-process capability provider registry (D16).

V0.1 scope (WP-033/034 subset):
  - register/unregister providers by capability
  - priority ordering + enable/disable
  - permission model enforced at registration (DIRECT_STATE_MUTATE rejected)
  - unified ExtensionUnavailableError

Plugin loading (WP-032) and RPC are post-Demo.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CapabilityType(str, enum.Enum):
    TOOL = "TOOL"
    RETRIEVAL = "RETRIEVAL"
    RESEARCH = "RESEARCH"
    WORKER_RUNTIME = "WORKER_RUNTIME"
    KNOWLEDGE = "KNOWLEDGE"
    LLM = "LLM"
    ARTIFACT_PROCESSOR = "ARTIFACT_PROCESSOR"


class Permission(str, enum.Enum):
    READ_ONLY = "READ_ONLY"
    ENRICH = "ENRICH"
    VALIDATE = "VALIDATE"
    CONTROL = "CONTROL"
    # DIRECT_STATE_MUTATE is intentionally ABSENT (WP-034 prohibition).


class ExtensionError(Exception):
    """Base error for extension failures."""


class ExtensionUnavailableError(ExtensionError):
    """No enabled provider for the requested capability."""


class Provider(Protocol):
    """Capability provider contract. Core modules depend only on this."""

    name: str
    capability: CapabilityType
    permission: Permission
    priority: int  # higher wins
    enabled: bool

    async def health(self) -> bool: ...

    async def invoke(self, payload: dict[str, Any]) -> Any: ...


@dataclass
class ProviderInfo:
    name: str
    capability: CapabilityType
    permission: Permission
    priority: int = 0
    enabled: bool = True
    provider: Provider | None = field(default=None, repr=False)


class ExtensionRegistry:
    """In-process provider registry."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderInfo] = {}

    def register(self, provider: Provider) -> ProviderInfo:
        # Permission guard: DIRECT_STATE_MUTATE does not exist in the enum,
        # but defend against raw-string providers too. Any invalid permission
        # (unknown string, or the forbidden one) is rejected with ExtensionError.
        perm = provider.permission
        if isinstance(perm, str) and perm == "DIRECT_STATE_MUTATE":
            raise ExtensionError(
                f"Provider {provider.name}: DIRECT_STATE_MUTATE is forbidden (WP-034)"
            )
        try:
            permission = Permission(perm)
        except ValueError as exc:
            raise ExtensionError(
                f"Provider {provider.name}: invalid permission {perm!r}"
            ) from exc
        info = ProviderInfo(
            name=provider.name,
            capability=provider.capability,
            permission=permission,
            priority=provider.priority,
            enabled=provider.enabled,
            provider=provider,
        )
        self._providers[provider.name] = info
        logger.info("Provider registered: %s (%s)", provider.name, provider.capability)
        return info

    def unregister(self, name: str) -> None:
        if name not in self._providers:
            raise ExtensionError(f"Provider not registered: {name}")
        del self._providers[name]

    def list_providers(
        self, capability: CapabilityType | None = None
    ) -> list[ProviderInfo]:
        infos = list(self._providers.values())
        if capability is not None:
            infos = [i for i in infos if i.capability == capability]
        return sorted(infos, key=lambda i: i.priority, reverse=True)

    def set_enabled(self, name: str, enabled: bool) -> None:
        info = self._providers.get(name)
        if info is None:
            raise ExtensionError(f"Provider not registered: {name}")
        info.enabled = enabled

    async def invoke(self, capability: CapabilityType, payload: dict[str, Any]) -> Any:
        """Invoke the highest-priority enabled provider for a capability."""
        for info in self.list_providers(capability):
            if not info.enabled:
                continue
            if info.provider is None:
                raise ExtensionError(
                    f"Provider {info.name} has no backing implementation"
                )
            return await info.provider.invoke(payload)
        raise ExtensionUnavailableError(f"No enabled provider for {capability.value}")

    async def health(self, name: str) -> bool:
        info = self._providers.get(name)
        if info is None or info.provider is None:
            raise ExtensionError(f"Provider not registered: {name}")
        return await info.provider.health()
