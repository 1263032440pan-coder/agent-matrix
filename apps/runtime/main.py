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

# 返修单-可靠性①: 心跳任务强引用（防 GC）
_BACKGROUND_HEARTBEATS: set = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 返修单-闭环接线 P0-3: 原生崩溃观测——faulthandler 在段错误/致命
    # 信号时直接写栈到文件（绕过 atexit 失效的硬杀场景——runtime 两次
    # 死因不明的解）
    import faulthandler

    _fh = open('data/runtime_faulthandler.log', 'a', encoding='utf-8')
    faulthandler.enable(file=_fh, all_threads=True)
    await check_db()
    await _check_migration_version()
    await _seed_agent_templates()
    # 返修单-可靠性①: REWORK 心跳——60s 扫活跃项目跑 advance。
    # 必须在 yield 之前 create_task，否则心跳只在 shutdown 才启动。
    import asyncio as _aio

    from sqlalchemy import select as _select

    async def _advance_heartbeat():
        from sqlalchemy import distinct

        from infrastructure.database import engine
        from project_runtime.execution.service import ExecutionRunner
        from project_runtime.project_state.enums import TaskStatus
        from project_runtime.project_state.models import Task as _Tk

        await _aio.sleep(2)
        while True:
            try:
                from sqlalchemy.ext.asyncio import AsyncSession as _Asess

                async with _Asess(engine) as _s:
                    pids = list((
                        await _s.execute(
                            _select(distinct(_Tk.project_id)).where(
                                _Tk.status == TaskStatus.REWORK
                            )
                        )
                    ).scalars())
                for pid in pids:
                    await ExecutionRunner()._auto_advance(pid)
            except Exception:  # noqa: BLE001 — 心跳绝不炸主循环
                pass
            await _aio.sleep(60)

    # 返修单-可靠性②: 僵尸 execution 恢复——runtime 重启后非终态
    # execution 无人消费（实测 exec9 RUNNING 卡 39 分钟, run() 对非
    # QUEUED 直接 return 救不活）。QUEUED 重新拉 runner, RUNNING
    # 判死清场（任务回 READY 由心跳/用户重提交接管）
    async def _orphan_recovery():
        from sqlalchemy import select as _sel
        from sqlalchemy.ext.asyncio import AsyncSession as _Asess2

        from infrastructure.database import engine as _eng
        from project_runtime.execution.models import Execution as _Ex
        from project_runtime.execution.service import ExecutionRunner

        async with _Asess2(_eng) as _s:
            zombies = list((
                await _s.execute(
                    _sel(_Ex).where(
                        _Ex.status.in_(["RUNNING", "QUEUED"])
                    )
                )
            ).scalars().all())
        if not zombies:
            return
        logger.info("orphan recovery: %d zombie execution(s)", len(zombies))
        async with _Asess2(_eng) as _s:
            for z in zombies:
                if z.status == "QUEUED":
                    # 未开跑——重新拉 runner
                    _bg = _aio.get_event_loop().create_task(
                        ExecutionRunner().run(z.id)
                    )
                    _BACKGROUND_HEARTBEATS.add(_bg)
                    _bg.add_done_callback(_BACKGROUND_HEARTBEATS.discard)
                else:
                    # RUNNING 中断——标记失败清场, 任务留给心跳重拉
                    z.status = "FAILED"
                    z.error_code = "ORPHAN_RECOVERY"
                    z.error_message = "runtime restart orphan"
            await _s.commit()

    _aio.get_event_loop().create_task(_orphan_recovery())

    _hb = _aio.get_event_loop().create_task(_advance_heartbeat())
    _BACKGROUND_HEARTBEATS.add(_hb)
    _hb.add_done_callback(_BACKGROUND_HEARTBEATS.discard)

    # 返修单v2 P5: 遗言日志——进程退出原因落盘（死要有尸体）
    import atexit
    import signal as _signal
    import traceback as _tb

    def _last_words(where: str = "exit") -> None:
        try:
            with open("data/runtime_last_words.log", "a", encoding="utf-8") as f:
                import datetime as _dt

                f.write(
                    f"{_dt.datetime.now().isoformat()} runtime exiting "
                    f"({where})\n{_tb.format_stack(limit=5)}\n"
                )
        except Exception:  # noqa: BLE001 — 遗言本身不能炸
            pass

    atexit.register(_last_words)

    def _on_term(signum, frame):
        _last_words(f"signal {signum}")
        raise SystemExit(128 + signum)

    try:
        _signal.signal(_signal.SIGTERM, _on_term)
        _signal.signal(_signal.SIGINT, _on_term)
    except (ValueError, OSError):
        pass  # 非主线程/平台不支持——atexit 兜底

    yield
    for t in list(_BACKGROUND_HEARTBEATS):
        t.cancel()


async def _seed_agent_templates() -> None:
    """启动时 seed 内置五 Agent 模板（幂等——20号规范 §2.1）。

    best-effort: 模板缺失只影响 Agent 实例化（任务照常走老路径），
    绝不阻断 runtime 启动。
    """
    try:
        from sqlalchemy.ext.asyncio import AsyncSession

        from project_runtime.worker.registry import seed_builtin_templates

        async with AsyncSession(engine) as session:
            count = await seed_builtin_templates(session)
            await session.commit()
        if count:
            logger.info("seeded %d built-in agent templates", count)
    except Exception:  # noqa: BLE001 — Agent 面绝不阻断启动
        logger.warning("agent template seed failed", exc_info=True)


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
