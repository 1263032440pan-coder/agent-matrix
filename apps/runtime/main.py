"""Project Autonomous Agent — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from infrastructure.config import settings
from infrastructure.database import check_db
from infrastructure.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await check_db()
    yield


app = FastAPI(
    title="Project Autonomous Agent",
    version="0.1.0",
    lifespan=lifespan,
)


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
