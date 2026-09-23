"""SQLite concurrency hardening utilities (WP-041 / 21-SQLite并发与事务规范).

- commit_with_retry: bounded retry (max 3) with exponential backoff + audit
  logging for transient SQLITE_BUSY / "database is locked". Never infinite,
  never used to mask long transactions (§8).
- short_tx: async context manager — begin/write/commit/close, milliseconds
  scale (§6). LLM/network calls must NOT happen inside.
"""

from __future__ import annotations

import asyncio
import logging
import time

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


async def commit_with_retry(session, *, context: str = "") -> None:
    """Commit with bounded retry on transient SQLite lock.

    Raises the last OperationalError after MAX_RETRIES attempts.
    """
    from sqlalchemy.exc import OperationalError

    for attempt in range(MAX_RETRIES + 1):
        try:
            await session.commit()
            return
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
            await session.rollback()
            await asyncio.sleep(backoff)
