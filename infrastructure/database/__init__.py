"""Project Autonomous Agent — Database initialization with SQLite WAL."""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from infrastructure.config import settings

logger = logging.getLogger(__name__)

# Async engine for runtime use
engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and foreign keys on every SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


async def check_db() -> bool:
    """Verify the database is accessible."""
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        logger.info("Database connection verified (SQLite WAL mode)")
        return True
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        return False
