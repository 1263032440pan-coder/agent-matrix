"""Shared composition root for the D16 ExtensionRegistry (SC01 bridge §3.4).

A single process-wide registry instance owned here; all consumers
(hook executor, retrieval orchestrator, search providers, worker tool
loop) register into and invoke through the SAME registry — no private
copies scattered across modules (WP-033 rule: one facade, many sources).
"""

from __future__ import annotations

from project_runtime.extension.registry import ExtensionRegistry

_registry: ExtensionRegistry | None = None


def shared_registry() -> ExtensionRegistry:
    """Process-wide singleton. Lazily created, never re-created."""
    global _registry
    if _registry is None:
        _registry = ExtensionRegistry()
    return _registry
