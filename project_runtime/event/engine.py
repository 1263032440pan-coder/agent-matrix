"""Event Engine (WP-042): record runtime events in short transactions.

- record(): opens a DEDICATED session, inserts one event, commits via
  commit_with_retry (bounded retry + audit). Never holds a tx across the
  caller's network calls.
- list_for_execution(): history read, supports after_id for reconnection
  recovery (spec §8).
- Live subscribers are served by the SSE endpoint (apps layer); the engine
  itself is persistence-only — Worker NEVER depends on subscriber health
  (spec §9 / WP-042 "SSE 断开不影响 Worker").

Lock-audit bridge (RH-02 复验衔接要求): commit_with_retry events are the
DB-06 audit trail; get_lock_audit() counters can be surfaced in diagnostics.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine, short_tx
from project_runtime.event.models import RuntimeEvent, RuntimeEventType

logger = logging.getLogger(__name__)


class EventEngine:
    """Persistence + query for runtime events. Persistence-only by design."""

    def __init__(self, engine=None):
        # Engine injectable for tests; defaults to the global production engine.
        self._engine = engine

    def _session(self) -> AsyncSession:
        return AsyncSession(self._engine or engine)

    async def record(
        self,
        event_type: RuntimeEventType,
        *,
        project_id: int | None = None,
        task_id: int | None = None,
        execution_id: int | None = None,
        phase: str | None = None,
        message: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
        causation_id: int | None = None,
        payload: dict | None = None,
    ) -> int | None:
        """Insert one event in its own short transaction. Returns event id.

        Failures are logged and swallowed: event persistence must never
        break the execution path (progress trail is best-effort audit).

        Message is truncated (RH-03 修改单瑕疵 1): the event table is
        append-heavy; full LLM output belongs to Execution.message (500) /
        TaskRun.result / Artifact files — events only POINT at it.
        """
        # Truncate: keep the event row small for SSE frames & list queries.
        if message and len(message) > 120:
            message = message[:120] + "…"
        payload_json = (
            json.dumps(payload, ensure_ascii=False) if payload else None
        )
        try:
            async with self._session() as session:
                event_id_box: dict[str, int | None] = {"id": None}

                async def _stage() -> None:
                    ev = RuntimeEvent(
                        event_type=event_type.value,
                        project_id=project_id,
                        task_id=task_id,
                        execution_id=execution_id,
                        phase=phase,
                        message=message,
                        actor=actor,
                        correlation_id=correlation_id,
                        causation_id=causation_id,
                        payload=payload_json,
                    )
                    session.add(ev)
                    await session.flush()
                    event_id_box["id"] = ev.id

                await _stage()
                await short_tx.commit_with_retry(
                    session,
                    context=f"event.{event_type.value}",
                    on_retry=_stage,
                )
                return event_id_box["id"]
        except Exception as e:  # noqa: BLE001 — progress trail is best-effort
            logger.error("Event record failed (%s): %s", event_type.value, e)
            return None

    async def list_for_execution(
        self, execution_id: int, after_id: int = 0, limit: int = 200
    ) -> list[RuntimeEvent]:
        """Ordered history for an execution; after_id enables reconnection."""
        stmt = (
            select(RuntimeEvent)
            .where(RuntimeEvent.execution_id == execution_id)
            .where(RuntimeEvent.id > after_id)
            .order_by(RuntimeEvent.id)
            .limit(limit)
        )
        async with self._session() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def latest_id(self, execution_id: int) -> int:
        stmt = (
            select(RuntimeEvent.id)
            .where(RuntimeEvent.execution_id == execution_id)
            .order_by(RuntimeEvent.id.desc())
            .limit(1)
        )
        async with self._session() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row or 0
