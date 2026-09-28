"""Project Memory Manager (WP-020 / 08-规范 架构).

五类长期 Project Memory 的统一访问层:

    State Memory      → Project/Task live state (read via Core managers)
    Artifact Memory   → ArtifactManager 产物视图
    Summary Memory    → hierarchical summaries (WP-021 entity; registry-ready)
    Retrieval Memory  → RetrievalOrchestrator (providers ONLY — never the
                        concrete retriever; WP-025 contract)
    Execution Memory  → runtime events + task runs (WP-042 store)

设计原则:
  - Manager 是访问层不是事实源——所有读写经既有 Core 组件。
  - Worker Session 退出不影响 Memory：manager 每次调用建立独立短事务
    session，不持有调用方的 session 生命周期（acceptance §3）。
  - Event / Artifact 作为 Memory Source：execution/artifact memory 直接
    从持久化存储读取（acceptance §4）。
"""

from __future__ import annotations

import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.retrieval.providers import RetrievalOrchestrator


class ProjectMemoryError(Exception):
    pass


class ProjectMemoryManager:
    """Unified access over the five long-lived memories."""

    def __init__(self, *, registry=None, engine=None):
        # Retrieval depends ONLY on the provider contract (WP-025) — the
        # orchestrator takes the unified registry, never a concrete retriever.
        self._registry = registry
        # Engine injectable (tests); defaults to the global runtime engine.
        self._engine = engine

    # ── session handling: independent short-lived sessions ─────────────

    def _session(self) -> AsyncSession:
        """Fresh session per call — Worker session exit never affects
        memory access (acceptance §3)."""
        return AsyncSession(self._engine or engine)

    # ── 1. State Memory ────────────────────────────────────────────────

    async def get_state_memory(self, project_id: int) -> dict:
        """Project + tasks live state (read-only view via Core models)."""
        from project_runtime.project_state.models import Project, Task

        async with self._session() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ProjectMemoryError(f"Project not found: {project_id}")
            tasks = (
                (
                    await s.execute(
                        select(Task).where(Task.project_id == project_id)
                    )
                )
                .scalars()
                .all()
            )
            # snapshot values INSIDE the session scope (before close)
            proj_view = {
                "id": project.id, "name": project.name,
                "status": project.status.value,
                "automation_mode": project.automation_mode.value,
            }
            task_views = [
                {"id": t.id, "name": t.name, "status": t.status.value}
                for t in tasks
            ]
        return {"project": proj_view, "tasks": task_views}

    # ── 2. Artifact Memory ─────────────────────────────────────────────

    async def get_artifact_memory(self, project_id: int) -> list[dict]:
        """Artifacts as memory (latest ACTIVE version per logical id)."""
        from project_runtime.project_state.models import Artifact

        async with self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(Artifact)
                        .where(Artifact.project_id == project_id)
                        .order_by(Artifact.logical_artifact_id, desc(Artifact.version))
                    )
                )
                .scalars()
                .all()
            )
            latest: dict[str, dict] = {}
            for a in rows:
                lid = a.logical_artifact_id
                if lid not in latest:
                    latest[lid] = {
                        "artifact_id": a.id, "logical_id": lid,
                        "name": a.name, "version": a.version,
                        "task_id": a.task_id, "sha256": a.sha256,
                        "path": a.path,
                    }
            return list(latest.values())

    # ── 3. Summary Memory (WP-021 entity; registered when available) ───

    async def get_summary_memory(self, project_id: int) -> list[dict]:
        """Hierarchical summaries. WP-021 delivers the entity; until then
        the memory layer returns an empty list (registry-ready — no fake
        data, no invented facts)."""
        try:
            from project_runtime.memory_summary.models import ProjectSummary
        except ImportError:
            return []
        async with self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(ProjectSummary).where(
                            ProjectSummary.project_id == project_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": r.id, "scope_type": r.scope_type,
                    "scope_key": r.scope_key, "summary": r.summary,
                    "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                    "source_version": r.source_version,
                }
                for r in rows
            ]

    # ── 4. Retrieval Memory (providers only — WP-025 contract) ─────────

    async def retrieve(
        self, query: str, *, project_id: int | None = None, limit: int = 10
    ) -> dict:
        """Route through the unified provider registry (acceptance §2)."""
        if self._registry is None:
            return {
                "query": query, "providers": [], "results": [],
                "total": 0, "errors": [],
            }
        async with self._session() as s:
            orch = RetrievalOrchestrator(s, self._registry)
            return await orch.search(query, project_id=project_id, limit=limit)

    # ── 5. Execution Memory (events + task runs) ───────────────────────

    async def get_execution_memory(
        self, project_id: int, *, limit: int = 50
    ) -> dict:
        """Runtime events (Event Store) + task runs as execution memory."""
        from project_runtime.event.models import RuntimeEvent
        from project_runtime.project_state.models import TaskRun

        async with self._session() as s:
            events = (
                (
                    await s.execute(
                        select(RuntimeEvent)
                        .where(RuntimeEvent.project_id == project_id)
                        .order_by(desc(RuntimeEvent.id))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            runs = (
                (
                    await s.execute(
                        select(TaskRun, TaskRun.task_id)
                        .where(
                            TaskRun.task_id.in_(
                                select(
                                    __import__(
                                        "project_runtime.project_state.models",
                                        fromlist=["Task"],
                                    ).Task.id
                                ).where(
                                    __import__(
                                        "project_runtime.project_state.models",
                                        fromlist=["Task"],
                                    ).Task.project_id == project_id
                                )
                            )
                        )
                        .order_by(desc(TaskRun.id))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            event_views = [
                {
                    "event_id": e.id, "event_type": e.event_type,
                    "task_id": e.task_id, "execution_id": e.execution_id,
                    "phase": e.phase, "message": (e.message or "")[:120],
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in reversed(events)
            ]
            run_views = [
                {"id": r.id, "task_id": r.task_id, "attempt": r.attempt}
                for r in reversed(runs)
            ]
        return {"events": event_views, "task_runs": run_views}

    # ── 6. Memory version ──────────────────────────────────────────────

    async def get_memory_version(self, project_id: int) -> dict:
        """Change watermark per memory class — monotonic pointers callers
        can use to detect 'has anything changed since I last looked'."""
        from project_runtime.event.models import RuntimeEvent
        from project_runtime.project_state.models import Artifact

        async with self._session() as s:
            latest_event = (
                await s.execute(
                    select(RuntimeEvent.id)
                    .where(RuntimeEvent.project_id == project_id)
                    .order_by(desc(RuntimeEvent.id))
                    .limit(1)
                )
            ).scalar_one_or_none()
            latest_artifact = (
                await s.execute(
                    select(Artifact.id)
                    .where(Artifact.project_id == project_id)
                    .order_by(desc(Artifact.id))
                    .limit(1)
                )
            ).scalar_one_or_none()
        return {
            "project_id": project_id,
            "state_version": latest_event or 0,
            "artifact_version": latest_artifact or 0,
            "retrieval_version": latest_artifact or 0,
            "summary_version": 0,  # WP-021 lands its own watermark
            "execution_version": latest_event or 0,
            "as_of": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
