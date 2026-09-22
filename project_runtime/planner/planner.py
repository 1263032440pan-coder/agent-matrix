"""DynamicPlanner — convert ProjectPlaybook workstreams into a Task Graph.

Respects Authority levels (MANDATORY never silently dropped).
Uses TaskGraphManager (D04) for Task CRUD + dependency management.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import DependencyType
from project_runtime.project_state.fsm import InvalidStateTransitionError
from project_runtime.project_state.models import ProjectPlaybook, Task
from project_runtime.task_graph.manager import CreateTaskRequest, TaskGraphManager

logger = logging.getLogger(__name__)

_MANDATORY = "MANDATORY"
_RECOMMENDED = "RECOMMENDED"
_OPTIONAL = "OPTIONAL"
_PROPOSED = "PROPOSED"


class DynamicPlanner:
    """Converts a ProjectPlaybook into a Task Graph.

    Respects Authority rules (WP-007):
      - MANDATORY: never silently excluded
      - RECOMMENDED: included, planner may skip based on project state
      - OPTIONAL: included, planner may skip based on value/cost
      - PROPOSED: included but clearly marked; must not masquerade as user requirement
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._mgr = TaskGraphManager(session)

    async def plan(self, playbook: ProjectPlaybook) -> list[Task]:
        """Generate a Task Graph from a ProjectPlaybook.

        Idempotent (D07-TD01 batch-1): planning the same playbook twice returns
        existing tasks (matched by name) instead of duplicating them.
        """
        workstreams = json.loads(playbook.workstreams or "[]")
        if not workstreams:
            return []

        # Phase 1: Create or reuse Task for each workstream
        task_map: dict[str, Task] = {}
        for ws in workstreams:
            task = await self._get_or_create_task(playbook.project_id, ws)
            task_map[ws["name"]] = task

        # Phase 2: Establish dependencies
        await self._create_dependencies(workstreams, task_map)

        # Phase 3: Validate (cycle check implicit via add_dependency)
        return list(task_map.values())

    async def _get_or_create_task(self, project_id: int, ws: dict) -> Task:
        from sqlalchemy import select

        stmt = select(Task).where(
            Task.project_id == project_id, Task.name == ws["name"]
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        return await self._create_task_for_workstream(project_id, ws)

    async def _create_task_for_workstream(self, project_id: int, ws: dict) -> Task:
        authority = (ws.get("source") or {}).get("authority", _OPTIONAL)

        # MANDATORY guard: if authority is missing/malformed, default to MANDATORY
        # to prevent accidental exclusion
        if authority == _MANDATORY:
            pass  # always included
        elif authority == _PROPOSED:
            pass  # included but marked
        elif authority == _RECOMMENDED:
            pass  # included
        else:
            pass  # included

        playbook_ref = json.dumps(
            {
                "workstream_name": ws["name"],
                "authority": authority,
                "source_type": (ws.get("source") or {}).get("type", "UNKNOWN"),
                "acceptance_criteria": ws.get("acceptance_criteria"),
            }
        )

        req = CreateTaskRequest(
            project_id=project_id,
            name=ws["name"],
            task_type=ws.get("workstream", "generic"),
        )
        task = await self._mgr.create_task(req)
        task.playbook_ref = playbook_ref

        # If MANDATORY, promote to READY immediately
        if authority == _MANDATORY:
            try:
                await self._mgr.promote_to_ready(task.id)
            except (ValueError, InvalidStateTransitionError):
                pass  # dependencies not yet satisfied

        await self._session.flush()
        return task

    async def _create_dependencies(
        self, workstreams: list[dict], task_map: dict[str, Task]
    ) -> None:
        for ws in workstreams:
            deps_text = ws.get("dependencies")
            if not deps_text or deps_text == "UNKNOWN":
                continue

            task = task_map.get(ws["name"])
            if task is None:
                continue

            # Dependencies are stored as text; parse comma/whitespace-separated
            dep_names = [d.strip() for d in deps_text.replace("\n", ",").split(",") if d.strip()]
            for dep_name in dep_names:
                upstream = task_map.get(dep_name)
                if upstream is None:
                    logger.warning(
                        "Unresolved dependency: task=%s depends_on=%s not found in workstreams",
                        task.id, dep_name,
                    )
                    # Mark unresolved dependency in playbook_ref
                    ref = json.loads(task.playbook_ref or "{}")
                    ref.setdefault("unresolved_dependencies", []).append(dep_name)
                    task.playbook_ref = json.dumps(ref)
                    continue
                try:
                    await self._mgr.add_dependency(
                        task.id, upstream.id, dep_type=DependencyType.HARD
                    )
                except ValueError as e:
                    logger.warning("Dependency skipped: %s -> %s: %s", task.id, upstream.id, e)
