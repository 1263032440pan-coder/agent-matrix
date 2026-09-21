"""Task Graph Manager — single entry point for Task CRUD, dependencies, and graph validation.

All Task state mutations go through StateTransitionService.
Direct Task.status writes are prohibited outside this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repository.base import BaseRepository
from project_runtime.project_state.enums import DependencyType, TaskStatus
from project_runtime.project_state.fsm import (
    InvalidStateTransitionError,
    StateTransitionService,
)
from project_runtime.project_state.models import Task, TaskDependency
from project_runtime.task_graph.graph import detect_cycle

logger = logging.getLogger(__name__)


@dataclass
class CreateTaskRequest:
    project_id: int
    name: str
    task_type: str = "generic"
    parent_task_id: int | None = None


@dataclass
class UpdateTaskRequest:
    name: str | None = None
    contract: str | None = None
    playbook_ref: str | None = None


# ── Manager ──────────────────────────────────────────────────────────────────


class TaskGraphManager:
    """Owns Task CRUD, dependency management, and graph invariants."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._task_repo = BaseRepository[Task](Task, session)
        self._fsm = StateTransitionService(session)

    # ── CRUD ─────────────────────────────────────────────────────────────

    async def create_task(self, req: CreateTaskRequest) -> Task:
        """Create a new Task in CREATED state."""
        task = Task(
            project_id=req.project_id,
            name=req.name,
            task_type=req.task_type,
            parent_task_id=req.parent_task_id,
            status=TaskStatus.CREATED,
        )
        await self._task_repo.add(task)
        logger.info("Task created: id=%s name=%s", task.id, req.name)
        return task

    async def get_task(self, task_id: int) -> Task | None:
        return await self._task_repo.get(task_id)

    async def list_tasks(self, project_id: int) -> list[Task]:
        stmt = select(Task).where(Task.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_task(self, task_id: int, req: UpdateTaskRequest) -> Task:
        """Update Task metadata.  Does NOT change status."""
        task = await self._get_or_raise(task_id)
        if req.name is not None:
            task.name = req.name
        if req.contract is not None:
            task.contract = req.contract
        if req.playbook_ref is not None:
            task.playbook_ref = req.playbook_ref
        await self._session.flush()
        return task

    async def cancel_task(self, task_id: int) -> Task:
        """Cancel a Task.  Must go through FSM."""
        task = await self._get_or_raise(task_id)
        await self._fsm.transition_task(task, TaskStatus.CANCELLED)
        await self._session.flush()
        return task

    # ── State transitions (delegated to FSM) ─────────────────────────────

    async def transition_task(self, task_id: int, to_status: TaskStatus) -> Task:
        task = await self._get_or_raise(task_id)
        await self._fsm.transition_task(task, to_status)
        await self._session.flush()
        return task

    # ── Dependencies ─────────────────────────────────────────────────────

    async def add_dependency(
        self, task_id: int, depends_on_id: int, dep_type: DependencyType = DependencyType.HARD
    ) -> TaskDependency:
        task = await self._get_or_raise(task_id)
        upstream = await self._get_or_raise(depends_on_id)

        if task.project_id != upstream.project_id:
            raise ValueError("Tasks must belong to the same project")

        if task_id == depends_on_id:
            raise ValueError("A task cannot depend on itself")

        existing = await self._get_dependency(task_id, depends_on_id)
        if existing:
            raise ValueError(f"Dependency already exists: {task_id} -> {depends_on_id}")

        # Check for cycle *before* inserting
        all_deps = await self._all_dependency_edges(task.project_id)
        all_deps.append((task_id, depends_on_id))
        cycle = detect_cycle(all_deps)
        if cycle:
            raise ValueError(f"Cycle detected: {' -> '.join(str(n) for n in cycle)}")

        dep = TaskDependency(task_id=task_id, depends_on_id=depends_on_id, dependency_type=dep_type)
        self._session.add(dep)
        await self._session.flush()
        logger.info("Dependency added: %s -> %s", task_id, depends_on_id)
        return dep

    async def remove_dependency(self, task_id: int, depends_on_id: int) -> None:
        dep = await self._get_dependency(task_id, depends_on_id)
        if dep is None:
            raise ValueError(f"Dependency not found: {task_id} -> {depends_on_id}")
        await self._session.delete(dep)
        await self._session.flush()

    async def get_dependencies(self, task_id: int) -> list[TaskDependency]:
        stmt = select(TaskDependency).where(TaskDependency.task_id == task_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_dependents(self, task_id: int) -> list[TaskDependency]:
        """Who depends on this task?"""
        stmt = select(TaskDependency).where(TaskDependency.depends_on_id == task_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Graph validation ─────────────────────────────────────────────────

    async def can_set_ready(self, task_id: int) -> bool:
        """Check if a Task's HARD dependencies are satisfied (all ACCEPTED)."""
        deps = await self.get_dependencies(task_id)
        for dep in deps:
            if dep.dependency_type != DependencyType.HARD:
                continue
            upstream = await self.get_task(dep.depends_on_id)
            if upstream is None or upstream.status != TaskStatus.ACCEPTED:
                return False
        return True

    async def promote_to_ready(self, task_id: int) -> Task:
        """Set Task to READY only if HARD deps are satisfied."""
        task = await self._get_or_raise(task_id)
        if task.status != TaskStatus.CREATED and task.status != TaskStatus.BLOCKED:
            raise InvalidStateTransitionError(
                "Task", task.id, task.status.value, TaskStatus.READY.value
            )

        # Verify HARD deps
        if not await self.can_set_ready(task_id):
            # Identify which dep is blocking
            blocking = await self._blocking_deps(task_id)
            raise ValueError(
                f"Cannot set READY: HARD dependencies not satisfied. "
                f"Blocking: {[d.depends_on_id for d in blocking]}"
            )

        await self._fsm.transition_task(task, TaskStatus.READY)
        await self._session.flush()
        return task

    async def check_graph_invariants(self, project_id: int) -> list[str]:
        """Validate graph structure: cycles, orphans, root tasks. Returns list of issues."""
        issues: list[str] = []

        tasks = await self.list_tasks(project_id)
        task_ids = {t.id for t in tasks}

        edges = await self._all_dependency_edges(project_id)

        # Cycle check
        cycle = detect_cycle(edges)
        if cycle:
            issues.append(f"Cycle detected: {' -> '.join(str(n) for n in cycle)}")

        # Orphan check — every task must be reachable from a root
        dep_ids: set[int] = set()
        for _, v in edges:
            dep_ids.add(v)
        source_ids: set[int] = {u for u, _ in edges}

        all_with_deps = dep_ids | source_ids

        for tid in task_ids:
            if tid not in all_with_deps and len(task_ids) > 1:
                issues.append(f"Orphan task (no dependencies): {tid}")

        # Root task check — at least one task with no incoming HARD deps
        has_root = False
        for t in tasks:
            deps = await self.get_dependencies(t.id)
            if not any(d.dependency_type == DependencyType.HARD for d in deps):
                has_root = True
                break
        if not has_root and len(tasks) > 0:
            issues.append("No root task found (every task has HARD dependencies)")

        return issues

    # ── helpers ──────────────────────────────────────────────────────────

    async def _get_or_raise(self, task_id: int) -> Task:
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task

    async def _get_dependency(self, task_id: int, depends_on_id: int) -> TaskDependency | None:
        stmt = select(TaskDependency).where(
            TaskDependency.task_id == task_id,
            TaskDependency.depends_on_id == depends_on_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _all_dependency_edges(self, project_id: int) -> list[tuple[int, int]]:
        tasks = await self.list_tasks(project_id)
        task_ids = [t.id for t in tasks]
        if not task_ids:
            return []
        stmt = select(TaskDependency).where(
            TaskDependency.task_id.in_(task_ids)
        )
        result = await self._session.execute(stmt)
        return [(d.task_id, d.depends_on_id) for d in result.scalars().all()]

    async def _blocking_deps(self, task_id: int) -> list[TaskDependency]:
        deps = await self.get_dependencies(task_id)
        blocking: list[TaskDependency] = []
        for dep in deps:
            if dep.dependency_type != DependencyType.HARD:
                continue
            upstream = await self.get_task(dep.depends_on_id)
            if upstream is None or upstream.status != TaskStatus.ACCEPTED:
                blocking.append(dep)
        return blocking
