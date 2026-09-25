"""Runtime Extension Plane contracts (WP-034 / 13-规范 §3 / 14-规范).

Five extension kinds with STRICT result contracts:

    Observer   → ACK               (no blocking power, READ_ONLY)
    Validator  → PASS/WARNING/FAIL (no state-write, VALIDATE)
    Middleware → new object        (ENRICH — returns transformed payload)
    Hook       → CONTINUE/PAUSE/RETRY/NEED_USER_INPUT/PROPOSAL (CONTROL)
    Policy     → Control results   (CONTROL, Core makes final call)

Red lines:
  - DIRECT_STATE_MUTATE forbidden (permission enum has no such value).
  - NEED_USER_INPUT only signals missing input — whether to wait for a human
    is decided by the Automation Mode Controller (never a third mode).
  - Any PROPOSAL goes through Core Validation → Transaction → State Mutation
    (the existing FSM actor guard is the enforcement point).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from project_runtime.extension.registry import Permission


class RuntimeExtensionKind(str, enum.Enum):
    OBSERVER = "Observer"
    VALIDATOR = "Validator"
    MIDDLEWARE = "Middleware"
    HOOK = "Hook"
    POLICY = "Policy"


class ObserverResult(str, enum.Enum):
    ACK = "ACK"


class ValidatorResult(str, enum.Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


class ControlResult(str, enum.Enum):
    """Hook / Policy results (04 规范 Runtime Extension Result)."""

    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    RETRY = "RETRY"
    NEED_USER_INPUT = "NEED_USER_INPUT"
    PROPOSAL = "PROPOSAL"
    # Validator-style results are NOT here — Hook may not PASS/FAIL state.


# Which permission each kind requires (14-规范 权限约束)
KIND_PERMISSION: dict[RuntimeExtensionKind, Permission] = {
    RuntimeExtensionKind.OBSERVER: Permission.READ_ONLY,
    RuntimeExtensionKind.VALIDATOR: Permission.VALIDATE,
    RuntimeExtensionKind.MIDDLEWARE: Permission.ENRICH,
    RuntimeExtensionKind.HOOK: Permission.CONTROL,
    RuntimeExtensionKind.POLICY: Permission.CONTROL,
}


class RuntimeExtensionError(Exception):
    """Base error for runtime-extension failures."""


class PermissionMismatchError(RuntimeExtensionError):
    """Extension invoked with a permission its kind does not allow."""


@dataclass
class ExtensionCall:
    """One invocation of a runtime extension at a hook point."""

    point: str                    # stable extension point id (14-规范)
    extension_id: str
    kind: RuntimeExtensionKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutcome:
    """Aggregated outcome of firing one hook point.

    The Core ALWAYS decides — outcomes are advisory. For PROPOSAL results
    the payload carries the proposal; state changes only happen via
    Core Validation → Transaction (never applied by this module).
    """

    point: str
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def control(self) -> ControlResult:
        """Strongest control result across all extensions (Core decides)."""
        priority = [
            ControlResult.PAUSE,
            ControlResult.RETRY,
            ControlResult.NEED_USER_INPUT,
            ControlResult.PROPOSAL,
        ]
        for wanted in priority:
            for r in self.results:
                if r.get("result") == wanted.value:
                    return wanted
        return ControlResult.CONTINUE

    @property
    def proposals(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r.get("result") == ControlResult.PROPOSAL.value]
