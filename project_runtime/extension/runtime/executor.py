"""Hook executor — fires stable extension points (WP-034 / 14-规范).

Stable point ids (14-规范 清单，稳定 ID 不可私自新增 —— AGENTS.md 约束 5).
Core call sites (scheduler/worker/gate/...) fire points through this module;
in-process extensions AND plugin-backed extensions (via the WP-033 bridge
pattern) both register against the same facade.
"""

from __future__ import annotations

import logging
from typing import Any

from project_runtime.extension.registry import ExtensionRegistry, Permission
from project_runtime.extension.runtime.contracts import (
    KIND_PERMISSION,
    ControlResult,
    ExtensionCall,
    HookOutcome,
    ObserverResult,
    PermissionMismatchError,
    RuntimeExtensionError,
    RuntimeExtensionKind,
    ValidatorResult,
)

logger = logging.getLogger(__name__)

# ── 稳定 Extension Point（14-规范；新增需 CR → ADR）─────────────────────
STABLE_POINTS: frozenset[str] = frozenset({
    "project.bootstrap.before",
    "project.bootstrap.after",
    "project.understanding.before",
    "project.understanding.after",
    "capability.assessment.after",
    "research.providers",
    "playbook.build.before",
    "playbook.build.after",
    "planner.before",
    "planner.after",
    "scheduler.before_dispatch",
    "context.providers",
    "context.middleware",
    "worker.before_execute",
    "worker.after_execute",
    "artifact.processors",
    "retrieval.providers",
    "gate.validators",
    "delivery.validators",
    "policy.extensions",
    "event.observers",
    # V1.9 Method Intelligence (14-规范, WP-037): registered via CR-METHOD-POINTS
    "method.assets.providers",
    "method.assets.processors",
    "procedure.normalizers",
})


class RuntimeHookExecutor:
    """Fires extension points against the unified registry.

    Extension registration shape (in-process or plugin-backed):
        registry.register_runtime(point, kind, impl)  — see register helper
    where impl is an object with:
        async def invoke(payload) -> dict with {"result": <contract value>}
    """

    def __init__(self, registry: ExtensionRegistry):
        self._registry = registry
        # (point, extension_id) → (kind, impl)
        self._extensions: dict[tuple[str, str], tuple[RuntimeExtensionKind, Any]] = {}

    def register_runtime(
        self, point: str, extension_id: str, kind: RuntimeExtensionKind, impl: Any
    ) -> None:
        if point not in STABLE_POINTS:
            raise RuntimeExtensionError(
                f"Unknown extension point: {point!r} — stable ids only "
                f"(new points require CR → ADR)"
            )
        self._extensions[(point, extension_id)] = (kind, impl)

    def unregister_runtime(self, point: str, extension_id: str) -> None:
        self._extensions.pop((point, extension_id), None)

    def _require_permission(self, kind: RuntimeExtensionKind, permission: Permission) -> None:
        """验收 §5: 权限不匹配时 Extension 调用被拒绝."""
        needed = KIND_PERMISSION[kind]
        if permission != needed:
            raise PermissionMismatchError(
                f"{kind.value} extension requires {needed.value}, "
                f"got {permission.value}"
            )

    def _attached(self, point: str) -> list[tuple[str, RuntimeExtensionKind, Any, Permission]]:
        """Extensions attached to a point, with their declared permissions."""
        out = []
        for (p, ext_id), (kind, impl) in self._extensions.items():
            if p != point:
                continue
            permission = getattr(impl, "permission", None)
            if permission is None:
                permission = KIND_PERMISSION[kind]
            out.append((ext_id, kind, impl, permission))
        return out

    async def fire_hook(self, point: str, payload: dict[str, Any]) -> HookOutcome:
        """Fire a hook point: run all attached extensions, aggregate results.

        Observer/Middleware/Validator results are collected; the aggregate
        CONTROL decision is advisory — the Core decides (Automation Mode
        Controller interprets NEED_USER_INPUT, never the extension).
        """
        outcome = HookOutcome(point=point)
        for ext_id, kind, impl, permission in self._attached(point):
            try:
                self._require_permission(kind, permission)
            except PermissionMismatchError:
                # Rejected BEFORE invocation (验收 §5) — recorded, not raised,
                # so one mis-declared extension cannot break the hook chain.
                logger.warning(
                    "Extension %s at %s rejected: permission mismatch", ext_id, point
                )
                outcome.results.append(
                    {"extension_id": ext_id, "result": "REJECTED_PERMISSION",
                     "kind": kind.value}
                )
                continue

            call = ExtensionCall(
                point=point, extension_id=ext_id, kind=kind, payload=payload
            )
            try:
                raw = await impl.invoke(call.payload) if not _is_runtime_impl(impl) \
                    else await impl.invoke(call)
                # Normalize: {"result": ...} envelope or bare enum/string
                result = _normalize_result(kind, raw)
                entry: dict = {
                    "extension_id": ext_id, "result": result, "kind": kind.value,
                }
                if isinstance(raw, dict) and raw.get("detail"):
                    entry["detail"] = raw["detail"]
                outcome.results.append(entry)
            except RuntimeExtensionError:
                raise
            except Exception as e:  # noqa: BLE001 — one extension failing
                # must not break the chain; recorded as EXTENSION_ERROR
                logger.warning("Extension %s at %s failed: %s", ext_id, point, e)
                outcome.results.append(
                    {"extension_id": ext_id, "result": "EXTENSION_ERROR",
                     "kind": kind.value, "detail": str(e)[:120]}
                )
        return outcome

    # Convenience: observer-style fire that can never block (ACK only)
    async def notify(self, point: str, payload: dict[str, Any]) -> HookOutcome:
        return await self.fire_hook(point, payload)


def _is_runtime_impl(impl: Any) -> bool:
    """Runtime-shaped impl takes ExtensionCall; plain impl takes payload."""
    import inspect

    try:
        sig = inspect.signature(impl.invoke)
        return "call" in sig.parameters
    except (TypeError, ValueError):
        return False


def _normalize_result(kind: RuntimeExtensionKind, raw: Any) -> str:
    """Coerce plugin/impl outputs into the kind's contract values."""
    value = None
    if isinstance(raw, dict):
        value = raw.get("result")
    else:
        value = raw
    if value is None:
        return "EXTENSION_ERROR"
    value = str(value)

    if kind == RuntimeExtensionKind.OBSERVER:
        return ObserverResult.ACK.value  # observers always ACK (no blocking)
    if kind == RuntimeExtensionKind.VALIDATOR:
        if value in {v.value for v in ValidatorResult}:
            return value
        return "EXTENSION_ERROR"  # validators may not return control values
    if kind in (RuntimeExtensionKind.HOOK, RuntimeExtensionKind.POLICY):
        if value in {v.value for v in ControlResult}:
            return value
        return "EXTENSION_ERROR"  # hooks may not return PASS/FAIL
    if kind == RuntimeExtensionKind.MIDDLEWARE:
        return "ACK"  # middleware returns the new object in raw["object"]
    return "EXTENSION_ERROR"
