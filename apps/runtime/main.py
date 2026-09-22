"""Project Autonomous Agent — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.runtime.api import projects_router
from infrastructure.config import settings
from infrastructure.database import check_db, engine
from infrastructure.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await check_db()
    await _check_migration_version()
    yield


async def _check_migration_version() -> None:
    """Warn if the DB schema is behind the alembic head (no auto-upgrade)."""
    import pathlib

    from sqlalchemy import text

    heads = list(pathlib.Path("alembic_migrations/versions").glob("*.py"))
    if not heads:
        return
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
        except Exception:
            logger.warning("alembic_version table missing — DB not migrated")
            return
    # Determine head revision from alembic env (single-head assumption)
    import alembic.config

    cfg = alembic.config.Config("alembic.ini")
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(cfg).get_current_head()
    if current != head:
        logger.warning(
            "DB schema behind: current=%s head=%s — run `alembic upgrade head`",
            current, head,
        )


app = FastAPI(
    title="Project Autonomous Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(projects_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.runtime.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
