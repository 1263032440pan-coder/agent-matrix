"""Summary service + generator (WP-021).

Lifecycle:
    trigger (artifact update / decision / issue change / task state change)
      → mark affected scopes STALE (source advanced past source_version)
      → refresh regenerates ONLY stale affected scopes (§3)

Summaries derive from live state via ProjectMemoryManager read APIs —
never written back into state (§4: not a source of truth).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.memory_summary.models import (
    ProjectSummary,
    SummaryScope,
    SummaryStatus,
)

logger = logging.getLogger(__name__)


class SummaryError(Exception):
    pass


class SummaryService:
    def __init__(self, session: AsyncSession):
        self._session = session

    # ── query ───────────────────────────────────────────────────────────

    async def get(self, project_id: int) -> list[ProjectSummary]:
        stmt = select(ProjectSummary).where(ProjectSummary.project_id == project_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_scope(
        self, project_id: int, scope_type: SummaryScope, scope_key: str = ""
    ) -> ProjectSummary | None:
        stmt = select(ProjectSummary).where(
            ProjectSummary.project_id == project_id,
            ProjectSummary.scope_type == scope_type.value,
            ProjectSummary.scope_key == scope_key,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ── STALE marking (trigger side) ────────────────────────────────────

    async def mark_stale(
        self,
        project_id: int,
        *,
        task_id: int | None = None,
        reason: str = "",
    ) -> int:
        """Mark summaries affected by a source change as STALE.

        A task-scoped change stales: its TASK summary + all ancestor scopes
        (MODULE → WORKSTREAM → PROJECT — parents aggregate children).
        A project-wide change stales everything under the project.
        Returns the number of summaries marked stale.
        """
        rows = await self.get(project_id)
        target_key = f"task:{task_id}" if task_id is not None else None
        count = 0
        for r in rows:
            affected = (
                target_key is None  # project-wide change → everything
                or r.scope_type == SummaryScope.TASK.value
                and r.scope_key == target_key
                or r.scope_type in (
                    SummaryScope.PROJECT.value,
                    SummaryScope.WORKSTREAM.value,
                    SummaryScope.MODULE.value,
                )
            )
            if affected and r.status != SummaryStatus.STALE.value:
                r.status = SummaryStatus.STALE.value
                count += 1
        await self._session.flush()
        if count:
            logger.info(
                "Summary STALE x%d (project=%s task=%s reason=%s)",
                count, project_id, task_id, reason or "-",
            )
        return count

    # ── refresh (scoped regeneration) ───────────────────────────────────

    async def refresh_scope(
        self,
        project_id: int,
        scope_type: SummaryScope,
        scope_key: str = "",
        *,
        summary: str,
        key_facts: list[str] | None = None,
        key_decisions: list[str] | None = None,
        open_issues: list[str] | None = None,
        task_refs: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        source_version: int,
    ) -> ProjectSummary:
        """Create/refresh ONE scope's summary. Only this scope is touched
        (acceptance §3 — unaffected scopes stay exactly as they were)."""
        row = await self.get_scope(project_id, scope_type, scope_key)
        if row is None:
            row = ProjectSummary(
                project_id=project_id,
                scope_type=scope_type.value,
                scope_key=scope_key,
            )
            self._session.add(row)

        row.summary = summary
        row.key_facts_json = _j(key_facts)
        row.key_decisions_json = _j(key_decisions)
        row.open_issues_json = _j(open_issues)
        row.task_refs_json = _j(task_refs)
        row.artifact_refs_json = _j(artifact_refs)
        row.evidence_refs_json = _j(evidence_refs)
        row.source_version = source_version
        row.status = SummaryStatus.FRESH.value
        await self._session.flush()
        return row

    async def stale_scopes(self, project_id: int) -> list[ProjectSummary]:
        stmt = select(ProjectSummary).where(
            ProjectSummary.project_id == project_id,
            ProjectSummary.status == SummaryStatus.STALE.value,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def latest_source_version(self, project_id: int) -> int:
        """Max source_version across the project's summaries (watermark)."""
        stmt = select(ProjectSummary.source_version).where(
            ProjectSummary.project_id == project_id
        )
        versions = (await self._session.execute(stmt)).scalars().all()
        return max(versions) if versions else 0


def _j(v: list[str] | None) -> str | None:
    return json.dumps(v, ensure_ascii=False) if v else None


def _engine_of(session: AsyncSession):
    """Extract the ASYNC engine from an AsyncSession (session.get_bind()
    returns the sync Engine — the RH-01 lesson)."""
    return session.bind


class SummaryGenerator:
    """Derives summaries from live sources (state/artifacts/events).

    V0.1: deterministic digest (no LLM) — counts + names + refs pulled from
    ProjectMemoryManager read APIs. The summary is a DERIVED VIEW: if the
    underlying sources change, mark_stale makes it stale until refreshed.
    """

    async def generate_project_summary(
        self, session: AsyncSession, project_id: int, source_version: int,
        *, engine=None,
    ) -> ProjectSummary:
        from project_runtime.memory.manager import ProjectMemoryManager

        mgr = ProjectMemoryManager(engine=engine or _engine_of(session))
        state = await mgr.get_state_memory(project_id)
        artifacts = await mgr.get_artifact_memory(project_id)

        tasks = state["tasks"]
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1

        status_line = "，".join(
            f"{k} {v}" for k, v in sorted(by_status.items())
        ) or "暂无任务"
        summary = (
            f"项目「{state['project']['name']}」当前共 {len(tasks)} 个任务"
            f"（{status_line}），产出 {len(artifacts)} 份产物。"
        )
        svc = SummaryService(session)
        return await svc.refresh_scope(
            project_id, SummaryScope.PROJECT, "",
            summary=summary,
            key_facts=[f"任务总数 {len(tasks)}", f"产物总数 {len(artifacts)}"],
            task_refs=[f"task:{t['id']}" for t in tasks[:50]],
            artifact_refs=[str(a["artifact_id"]) for a in artifacts[:50]],
            source_version=source_version,
        )

    async def generate_task_summary(
        self,
        session: AsyncSession,
        project_id: int,
        task_id: int,
        source_version: int,
        *,
        engine=None,
    ) -> ProjectSummary:
        from project_runtime.memory.manager import ProjectMemoryManager

        mgr = ProjectMemoryManager(engine=engine or _engine_of(session))
        state = await mgr.get_state_memory(project_id)
        task = next(
            (t for t in state["tasks"] if t["id"] == task_id), None
        )
        if task is None:
            raise SummaryError(f"Task not found: {task_id}")

        arts = await mgr.get_artifact_memory(project_id)
        task_arts = [a for a in arts if a["task_id"] == task_id]

        summary = (
            f"任务「{task['name']}」当前状态 {task['status']}，"
            f"产出 {len(task_arts)} 份产物。"
        )
        svc = SummaryService(session)
        return await svc.refresh_scope(
            project_id, SummaryScope.TASK, f"task:{task_id}",
            summary=summary,
            key_facts=[f"状态 {task['status']}"],
            task_refs=[f"task:{task_id}"],
            artifact_refs=[str(a["artifact_id"]) for a in task_arts],
            source_version=source_version,
        )
