"""Tool name wire adapter — canonical (tool:x) ↔ gateway-safe wire names."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

ToolNameStyle = Literal["colon", "underscore", "dot", "none"]


@dataclass
class ToolNameMap:
    wire_to_canonical: dict[str, str] = field(default_factory=dict)
    canonical_to_wire: dict[str, str] = field(default_factory=dict)


class ToolNameAdapter:
    """Encode tool function names for strict OpenAI-compatible gateways.

    Default style is ``underscore`` (``tool:read_file`` → ``tool_read_file``).
    """

    def __init__(self, style: ToolNameStyle = "underscore"):
        if style == "none":
            raise ValueError("style=none cannot adapt tools")
        self.style: ToolNameStyle = style
        self._map = ToolNameMap()

    def to_wire(self, canonical: str) -> str:
        if self.style == "colon" or ":" not in canonical:
            wire = canonical
        elif self.style == "underscore":
            wire = canonical.replace(":", "_")
        elif self.style == "dot":
            wire = canonical.replace(":", ".")
        else:
            wire = canonical.replace(":", "_")
        # register for reverse lookup
        existing = self._map.wire_to_canonical.get(wire)
        if existing is not None and existing != canonical:
            raise ValueError(f"wire collision: {wire!r} <- {existing!r} vs {canonical!r}")
        self._map.wire_to_canonical[wire] = canonical
        self._map.canonical_to_wire[canonical] = wire
        return wire

    def to_internal(self, wire: str) -> str:
        """Alias for to_canonical (matches early unit tests)."""
        return self.to_canonical(wire)

    def to_canonical(self, wire: str) -> str:
        if wire in self._map.wire_to_canonical:
            return self._map.wire_to_canonical[wire]
        if wire in self._map.canonical_to_wire:
            return wire
        return wire

    def adapt_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if tools is None:
            return None
        out: list[dict] = []
        for item in tools:
            cloned = copy.deepcopy(item)
            fn = cloned.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                fn["name"] = self.to_wire(str(fn["name"]))
            out.append(cloned)
        return out
