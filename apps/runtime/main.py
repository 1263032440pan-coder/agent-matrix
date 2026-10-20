"""Project Autonomous Agent — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.runtime.api import executions_router, projects_router, settings_router
from apps.runtime.api.agents import router as agents_router
from apps.runtime.api.artifacts import router as artifacts_router
from apps.runtime.api.checkpoints import router as checkpoints_router
from apps.runtime.api.pipelines import router as pipelines_router
from apps.runtime.api.plugins import router as plugins_router
from apps.runtime.api.skills import router as skills_router
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


class SchemaBehindError(RuntimeError):
    """DB schema is behind alembic head — runtime refuses to start (F2/RH-02)."""


async def _check_migration_version() -> None:
    """Refuse to start when the DB schema is behind the alembic head.

    F2 (RH-02 复验遗留): previously only warned, then endpoints failed with
    'no such table: execution' → 500. Now raises with a clear fix hint.
    """
    import pathlib

    from sqlalchemy import text

    heads = list(pathlib.Path("alembic_migrations/versions").glob("*.py"))
    if not heads:
        return
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
        except Exception as e:
            raise SchemaBehindError(
                "alembic_version table missing — DB not migrated. "
                "Run `alembic upgrade head` before starting the runtime."
            ) from e
    # Determine head revision from alembic env (single-head assumption)
    import alembic.config

    cfg = alembic.config.Config("alembic.ini")
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(cfg).get_current_head()
    if current != head:
        raise SchemaBehindError(
            f"DB schema behind: current={current} head={head} — "
            "Run `alembic upgrade head` before starting the runtime."
        )


app = FastAPI(
    title="Project Autonomous Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",  # Tauri 2 desktop webview origin
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(agents_router)
app.include_router(projects_router)
app.include_router(executions_router)
app.include_router(skills_router)
app.include_router(checkpoints_router)
app.include_router(plugins_router)
app.include_router(pipelines_router)
app.include_router(artifacts_router)
app.include_router(settings_router)


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
