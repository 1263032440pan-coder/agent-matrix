"""SQLite concurrency hardening utilities (WP-041 / 21-SQLite并发与事务规范).

- commit_with_retry: bounded retry (max 3) with exponential backoff + audit
  logging for transient SQLITE_BUSY / "database is locked". Never infinite,
  never used to mask long transactions (§8).
- short_tx: async context manager — begin/write/commit/close, milliseconds
  scale (§6). LLM/network calls must NOT happen inside.

Important: session.rollback() on lock clears pending ORM mutations. Callers
that stage writes MUST pass ``on_retry`` to re-apply them; otherwise a bare
retry would empty-commit and silently drop the work (READY→RUNNING lost →
later INVALID_STATE_TRANSITION READY→EXECUTION_DONE).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF_S = 0.05

# Audit counters (in-process, DB-06 evidence): surfaced via get_lock_audit().
_lock_events: list[dict] = []


def get_lock_audit() -> list[dict]:
    """Return recorded lock-retry events (audit trail for DB-06)."""
    return list(_lock_events)


def reset_lock_audit() -> None:
    _lock_events.clear()


async def commit_with_retry(
    session,
    *,
    context: str = "",
    on_retry: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Commit with bounded retry on transient SQLite lock.

    ``on_retry`` is invoked after each lock-triggered rollback so the caller
    can re-stage ORM mutations that rollback discarded.

    Raises the last OperationalError after MAX_RETRIES attempts.
    """
    from sqlalchemy.exc import OperationalError

    lost_pending = False

    for attempt in range(MAX_RETRIES + 1):
        try:
            await session.commit()
        except OperationalError as e:
            if "locked" not in str(e).lower() or attempt == MAX_RETRIES:
                raise
            backoff = BASE_BACKOFF_S * (2**attempt)
            _lock_events.append(
                {
                    "ts": time.time(),
                    "context": context,
                    "attempt": attempt + 1,
                    "backoff_s": backoff,
                }
            )
            logger.warning(
                "SQLite lock on commit (%s) — retry %d/%d in %.2fs",
                context,
                attempt + 1,
                MAX_RETRIES,
                backoff,
            )
            had_pending = bool(session.new or session.dirty or session.deleted)
            await session.rollback()
            if on_retry is not None:
                await on_retry()
                lost_pending = False
            elif had_pending:
                lost_pending = True
            await asyncio.sleep(backoff)
            continue

        # commit() returned without error
        if lost_pending:
            raise OperationalError(
                "commit_with_retry lost pending work",
                {},
                Exception(
                    f"pending ORM mutations discarded after lock rollback "
                    f"({context}); pass on_retry to re-stage"
                ),
            )
        return
