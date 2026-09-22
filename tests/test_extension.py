"""D16 Minimal Extension Registry — register/query/priority/enable/errors."""

from typing import Any

import pytest

from project_runtime.extension.registry import (
    CapabilityType,
    ExtensionError,
    ExtensionRegistry,
    ExtensionUnavailableError,
    Permission,
)


class FakeProvider:
    def __init__(
        self,
        name: str,
        capability: CapabilityType,
        permission: Permission = Permission.READ_ONLY,
        priority: int = 0,
        enabled: bool = True,
        result: Any = "ok",
    ):
        self.name = name
        self.capability = capability
        self.permission = permission
        self.priority = priority
        self.enabled = enabled
        self._result = result

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict[str, Any]) -> Any:
        return {"provider": self.name, "result": self._result, "payload": payload}


# ════════════════════════════════════════════════════════════════════════════
# Register / unregister / query
# ════════════════════════════════════════════════════════════════════════════


def test_register_and_list_by_capability():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("t1", CapabilityType.TOOL))
    reg.register(FakeProvider("r1", CapabilityType.RETRIEVAL))

    tools = reg.list_providers(CapabilityType.TOOL)
    assert [p.name for p in tools] == ["t1"]
    assert len(reg.list_providers()) == 2


def test_unregister():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("t1", CapabilityType.TOOL))
    reg.unregister("t1")
    assert reg.list_providers() == []
    with pytest.raises(ExtensionError, match="not registered"):
        reg.unregister("t1")


# ════════════════════════════════════════════════════════════════════════════
# Priority + enable/disable
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_invoke_uses_highest_priority_enabled():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("low", CapabilityType.LLM, priority=1))
    reg.register(FakeProvider("high", CapabilityType.LLM, priority=10))

    out = await reg.invoke(CapabilityType.LLM, {"q": 1})
    assert out["provider"] == "high"


@pytest.mark.asyncio
async def test_disabled_provider_skipped():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("high", CapabilityType.LLM, priority=10, enabled=False))
    reg.register(FakeProvider("low", CapabilityType.LLM, priority=1))

    out = await reg.invoke(CapabilityType.LLM, {})
    assert out["provider"] == "low"


@pytest.mark.asyncio
async def test_set_enabled_toggle():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("p", CapabilityType.TOOL, enabled=False))
    with pytest.raises(ExtensionUnavailableError):
        await reg.invoke(CapabilityType.TOOL, {})

    reg.set_enabled("p", True)
    out = await reg.invoke(CapabilityType.TOOL, {})
    assert out["provider"] == "p"


# ════════════════════════════════════════════════════════════════════════════
# Unified errors
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_invoke_no_provider_raises_unavailable():
    reg = ExtensionRegistry()
    with pytest.raises(ExtensionUnavailableError):
        await reg.invoke(CapabilityType.RESEARCH, {})


# ════════════════════════════════════════════════════════════════════════════
# Permission guard
# ════════════════════════════════════════════════════════════════════════════


def test_direct_state_mutate_rejected():
    class BadProvider(FakeProvider):
        def __init__(self):
            super().__init__("bad", CapabilityType.TOOL)
            self.permission = "DIRECT_STATE_MUTATE"  # raw string bypass

    reg = ExtensionRegistry()
    with pytest.raises(ExtensionError, match="DIRECT_STATE_MUTATE"):
        reg.register(BadProvider())


def test_invalid_permission_string_rejected_with_extension_error():
    class WeirdProvider(FakeProvider):
        def __init__(self):
            super().__init__("weird", CapabilityType.TOOL)
            self.permission = "SUPER_USER"  # unknown raw string

    reg = ExtensionRegistry()
    with pytest.raises(ExtensionError, match="invalid permission"):
        reg.register(WeirdProvider())


@pytest.mark.asyncio
async def test_invoke_provider_without_implementation_raises_extension_error():
    reg = ExtensionRegistry()
    p = FakeProvider("ghost", CapabilityType.TOOL)
    reg.register(p)
    # Simulate a registered-but-hollow provider
    reg._providers["ghost"].provider = None
    with pytest.raises(ExtensionError, match="no backing implementation"):
        await reg.invoke(CapabilityType.TOOL, {})


def test_four_permissions_registerable():
    reg = ExtensionRegistry()
    for i, perm in enumerate(
        (Permission.READ_ONLY, Permission.ENRICH, Permission.VALIDATE, Permission.CONTROL)
    ):
        reg.register(FakeProvider(f"p{i}", CapabilityType.TOOL, permission=perm))
    assert len(reg.list_providers()) == 4


# ════════════════════════════════════════════════════════════════════════════
# Health
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health():
    reg = ExtensionRegistry()
    reg.register(FakeProvider("h", CapabilityType.TOOL))
    assert await reg.health("h") is True
    with pytest.raises(ExtensionError):
        await reg.health("nope")
