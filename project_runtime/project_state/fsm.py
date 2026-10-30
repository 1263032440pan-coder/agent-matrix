"""StateTransitionService — the single point of state mutation for Project and Task.

All state changes MUST go through this service.  Direct assignment
(project.status = ..., task.status = ...) is prohibited outside this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from project_runtime.project_state.enums import ProjectStatus, TaskStatus

logger = logging.getLogger(__name__)

# ── legal transition tables (as-per 03-核心领域模型.md) ────────────────────

_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.EXECUTION_DONE,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.EXECUTION_DONE: {TaskStatus.ACCEPTED, TaskStatus.REWORK},
    TaskStatus.REWORK: {TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {
        TaskStatus.READY,
        TaskStatus.REWORK,  # SYS-143: 仅 actor=user
        TaskStatus.CANCELLED,
    },
    TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.REWORK, TaskStatus.CANCELLED},
    # ACCEPTED 仍属终态集合；表上 ACCEPTED→REWORK 仅供 actor=user（见 transition_task）
    TaskStatus.ACCEPTED: {TaskStatus.REWORK},
    TaskStatus.CANCELLED: set(),  # terminal
}

_PROJECT_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.CREATED: {ProjectStatus.INITIALIZING, ProjectStatus.CANCELLED},
    ProjectStatus.INITIALIZING: {
        ProjectStatus.ACTIVE,
        ProjectStatus.BLOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ACTIVE: {
        ProjectStatus.BLOCKED,
        ProjectStatus.COMPLETING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.BLOCKED: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
    ProjectStatus.COMPLETING: {
        ProjectStatus.COMPLETED,
        ProjectStatus.ACTIVE,
        ProjectStatus.BLOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.COMPLETED: set(),  # terminal
    ProjectStatus.CANCELLED: set(),  # terminal
}

_TERMINAL_TASK = {TaskStatus.ACCEPTED, TaskStatus.CANCELLED}
_TERMINAL_PROJECT = {ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}


# ── result types ────────────────────────────────────────────────────────────


@dataclass
class TransitionResult:
    allowed: bool
    from_status: str
    to_status: str
    reason: str = ""


class InvalidStateTransitionError(ValueError):
    """Raised when a state transition is not allowed."""

    def __init__(self, entity_type: str, entity_id: int, from_status: str, to_status: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"INVALID_STATE_TRANSITION: {entity_type}#{entity_id} "
            f"{from_status} -> {to_status}"
        )


# ── the service ─────────────────────────────────────────────────────────────


class StateTransitionService:
    """Core FSM Service — the only place allowed to mutate Project.status / Task.status."""

    def __init__(self, session, event_persist: Callable | None = None):
        self._session = session
        self._event_persist = event_persist

    # ── Task ────────────────────────────────────────────────────────────

    def validate_task(
        self, from_status: TaskStatus, to_status: TaskStatus
    ) -> TransitionResult:
        allowed = to_status in _TASK_TRANSITIONS.get(from_status, set())
        return TransitionResult(
            allowed=allowed,
            from_status=from_status.value,
            to_status=to_status.value,
            reason=(
                ""
                if allowed
                else f"Illegal transition: {from_status.value} -> {to_status.value}"
            ),
        )

    async def transition_task(
        self,
        task: object,  # Task model instance
        to_status: TaskStatus,
        *,
        command_id: str = "",
        expected_from: TaskStatus | None = None,
        actor: str = "SYSTEM",
    ) -> None:
        """Atomically transition a Task to a new status.

        Raises InvalidStateTransitionError on illegal transitions.
        Performs status mutation + optional event persist in a single
        logical unit (caller wraps in transaction).

        Actor guard (D11-TD01): EXECUTION_DONE → ACCEPTED is restricted
        to actor == "GATE" (GateCore). All other callers are rejected.

        SYS-143: ACCEPTED → REWORK / BLOCKED → REWORK only when actor == "user".
        ACCEPTED stays in _TERMINAL_TASK; non-user exits are refused at the
        terminal guard before the transition table is consulted.
        """
        from_status = task.status
        if isinstance(from_status, str):
            from_status = TaskStatus(from_status)

        # Actor guard: only GATE may accept a task
        if (
            from_status == TaskStatus.EXECUTION_DONE
            and to_status == TaskStatus.ACCEPTED
            and actor != "GATE"
        ):
            logger.warning(
                "ACTOR_GUARD_REJECT task#%s %s -> %s actor=%s",
                task.id, from_status.value, to_status.value, actor,
            )
            raise InvalidStateTransitionError(
                "Task", task.id, from_status.value, to_status.value
            )

        # SYS-143: user-only reopen from ACCEPTED / BLOCKED → REWORK
        if (
            from_status in (TaskStatus.ACCEPTED, TaskStatus.BLOCKED)
            and to_status == TaskStatus.REWORK
            and actor != "user"
        ):
            logger.warning(
                "ACTOR_GUARD_REJECT task#%s %s -> %s actor=%s",
                task.id, from_status.value, to_status.value, actor,
            )
            raise InvalidStateTransitionError(
                "Task", task.id, from_status.value, to_status.value
            )

        # Optimistic concurrency guard
        if expected_from is not None and from_status != expected_from:
            raise InvalidStateTransitionError(
                "Task", task.id, from_status.value, to_status.value
            )

        # Terminal guard — ACCEPTED stays terminal except user → REWORK
        if from_status in _TERMINAL_TASK:
            user_reopen = (
                from_status == TaskStatus.ACCEPTED
                and to_status == TaskStatus.REWORK
                and actor == "user"
            )
            if not user_reopen:
                raise InvalidStateTransitionError(
                    "Task", task.id, from_status.value, to_status.value
                )

        result = self.validate_task(from_status, to_status)
        if not result.allowed:
            logger.warning(
                "INVALID_STATE_TRANSITION task#%s %s -> %s",
                task.id,
                from_status.value,
                to_status.value,
            )
            raise InvalidStateTransitionError(
                "Task", task.id, from_status.value, to_status.value
            )

        task.status = to_status

        if self._event_persist:
            await self._event_persist(
                entity_type="Task",
                entity_id=task.id,
                from_status=from_status.value,
                to_status=to_status.value,
                command_id=command_id,
            )

    # ── Project ─────────────────────────────────────────────────────────

    def validate_project(
        self, from_status: ProjectStatus, to_status: ProjectStatus
    ) -> TransitionResult:
        allowed = to_status in _PROJECT_TRANSITIONS.get(from_status, set())
        return TransitionResult(
            allowed=allowed,
            from_status=from_status.value,
            to_status=to_status.value,
            reason=(
                ""
                if allowed
                else f"Illegal transition: {from_status.value} -> {to_status.value}"
            ),
        )

    async def transition_project(
        self,
        project: object,  # Project model instance
        to_status: ProjectStatus,
        *,
        command_id: str = "",
        expected_from: ProjectStatus | None = None,
    ) -> None:
        from_status = project.status
        if isinstance(from_status, str):
            from_status = ProjectStatus(from_status)

        if expected_from is not None and from_status != expected_from:
            raise InvalidStateTransitionError(
                "Project", project.id, from_status.value, to_status.value
            )

        if from_status in _TERMINAL_PROJECT:
            raise InvalidStateTransitionError(
                "Project", project.id, from_status.value, to_status.value
            )

        result = self.validate_project(from_status, to_status)
        if not result.allowed:
            logger.warning(
                "INVALID_STATE_TRANSITION project#%s %s -> %s",
                project.id,
                from_status.value,
                to_status.value,
            )
            raise InvalidStateTransitionError(
                "Project", project.id, from_status.value, to_status.value
            )

        project.status = to_status

        if self._event_persist:
            await self._event_persist(
                entity_type="Project",
                entity_id=project.id,
                from_status=from_status.value,
                to_status=to_status.value,
                command_id=command_id,
            )
