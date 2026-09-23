"""GateCore — acceptance pipeline for EXECUTION_DONE tasks.

Pipeline: EXECUTION_DONE → Artifact Collection → Validators → Gate Result.
Only GateCore may transition EXECUTION_DONE → ACCEPTED (D11-TD01 actor guard).
"""

from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import GateStatus, TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Artifact, GateResult, Task

logger = logging.getLogger(__name__)


# ── Validator interface ────────────────────────────────────────────────────


@dataclass
class ValidatorResult:
    status: GateStatus  # PASS / WARNING treated as PASS, FAIL
    message: str = ""


class Validator(ABC):
    """Validators return PASS/WARNING/FAIL. They NEVER touch Task state."""

    name: str = "base"

    @abstractmethod
    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        ...


class FileValidator(Validator):
    """Real check: artifact files exist on disk and SHA-256 matches."""

    name = "file"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        if not artifacts:
            return ValidatorResult(GateStatus.FAIL, "no artifacts produced")
        for a in artifacts:
            if not os.path.exists(a.path):
                return ValidatorResult(GateStatus.FAIL, f"artifact file missing: {a.path}")
            if a.sha256:
                actual = hashlib.sha256(open(a.path, "rb").read()).hexdigest()
                if actual != a.sha256:
                    return ValidatorResult(
                        GateStatus.FAIL, f"sha256 mismatch for {a.path}"
                    )
        return ValidatorResult(GateStatus.PASS, f"{len(artifacts)} artifacts verified")


class BuildValidator(Validator):
    """V0.1 placeholder — no real build step. Always PASS."""

    name = "build"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        return ValidatorResult(GateStatus.PASS, "build skipped (V0.1)")


class TestValidator(Validator):
    """V0.1 placeholder — no real test step. Always PASS."""

    name = "test"

    async def validate(self, task: Task, artifacts: list[Artifact]) -> ValidatorResult:
        return ValidatorResult(GateStatus.PASS, "tests skipped (V0.1)")


# ── GateCore ───────────────────────────────────────────────────────────────


class GateCore:
    """Evaluates EXECUTION_DONE tasks and transitions them per Gate result.

    Actor guard (D11-TD01): only GateCore may call EXECUTION_DONE → ACCEPTED.
    """

    ACTOR = "GATE"

    def __init__(self, session: AsyncSession, validators: list[Validator] | None = None):
        self._session = session
        self._fsm = StateTransitionService(session)
        self._validators: list[Validator] = validators or [
            FileValidator(),
            BuildValidator(),
            TestValidator(),
        ]

    async def evaluate(self, task_id: int) -> GateResult:
        stmt = select(Task).where(Task.id == task_id)
        task = (await self._session.execute(stmt)).scalar_one()
        if task.status != TaskStatus.EXECUTION_DONE:
            raise ValueError(
                f"Gate requires EXECUTION_DONE, got {task.status.value}"
            )

        # WP-042: GATE_STARTED event (best-effort, never blocks the gate path)
        try:
            from project_runtime.event import EventEngine, RuntimeEventType

            await EventEngine().record(
                RuntimeEventType.GATE_STARTED,
                project_id=task.project_id,
                task_id=task_id,
                phase="GATE",
                actor=self.ACTOR,
                message=f"gate_core evaluating task {task_id}",
            )
        except Exception:  # noqa: BLE001
            pass

        # Artifact collection
        stmt = select(Artifact).where(Artifact.task_id == task.id)
        artifacts = list((await self._session.execute(stmt)).scalars().all())

        # Run validators
        details: list[str] = []
        final = GateStatus.PASS
        for v in self._validators:
            r = await v.validate(task, artifacts)
            details.append(f"[{v.name}] {r.status.value}: {r.message}")
            if r.status == GateStatus.FAIL:
                final = GateStatus.FAIL
                break  # fail fast
            if r.status == GateStatus.BLOCKED and final == GateStatus.PASS:
                final = GateStatus.BLOCKED

        # Persist GateResult
        gate_result = GateResult(
            task_id=task.id,
            gate_name="gate_core",
            status=final,
            details="\n".join(details),
        )
        self._session.add(gate_result)

        # FSM transition (actor-guarded: this is the ONLY ACCEPTED path)
        # V1.11 FSM: EXECUTION_DONE → ACCEPTED | REWORK only.
        # BLOCKED / NEED_APPROVAL both land on REWORK (GateResult.status
        # preserves the distinction for downstream ManualAction).
        if final == GateStatus.PASS:
            await self._fsm.transition_task(task, TaskStatus.ACCEPTED, actor=self.ACTOR)
        else:
            await self._fsm.transition_task(task, TaskStatus.REWORK, actor=self.ACTOR)

        await self._session.flush()
        logger.info("Gate %s → %s for task %d", "gate_core", final.value, task_id)

        # WP-042: GATE_PASSED / GATE_FAILED event (best-effort)
        try:
            from project_runtime.event import EventEngine, RuntimeEventType

            await EventEngine().record(
                RuntimeEventType.GATE_PASSED
                if final == GateStatus.PASS
                else RuntimeEventType.GATE_FAILED,
                project_id=task.project_id,
                task_id=task_id,
                phase="GATE",
                actor=self.ACTOR,
                message=f"gate_core → {final.value}",
                payload={"details": "\n".join(details)[:500]},
            )
        except Exception:  # noqa: BLE001
            pass

        return gate_result
