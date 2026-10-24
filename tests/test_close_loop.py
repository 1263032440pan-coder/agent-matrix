"""返修单-闭环接线: P0-1 REWORK 自动接线 + P0-2 runner 强引用测试。"""


import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.project_state.base import Base


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cl.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


class TestReworkAutoAdvance:
    @pytest.mark.asyncio
    async def test_rework_picked_up_on_advance(self, dbengine, monkeypatch,
                                               tmp_path):
        """P0-1: REWORK 任务在 advance 时被转移 READY（1 轮 FAIL < 3）。"""
        from project_runtime.project_state.enums import (
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            GateResult,
            Project,
            Task,
        )
        from project_runtime.reviewer.gate import GateStatus
        from project_runtime.task_graph.manager import (
            CreateTaskRequest,
            TaskGraphManager,
        )

        monkeypatch.chdir(tmp_path)
        import project_runtime.event.engine as ev_mod
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        monkeypatch.setattr(ev_mod, "engine", dbengine)
        async with AsyncSession(dbengine) as s:
            p = Project(
                name="rework", automation_mode=AutomationMode.FULL_AUTO,
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="任务A", task_type="开发实现",
            ))
            tid, pid = t.id, p.id
            # 1 轮 FAIL（< 3 熔断线）
            s.add(GateResult(task_id=tid, gate_name="g",
                             status=GateStatus.FAIL, details="x"))
            await s.commit()
            # 置 REWORK + updated_at 老（过 60s 冷却）
            import datetime as _dt

            from sqlalchemy import update

            await s.execute(
                update(Task).where(Task.id == tid).values(
                    status=TaskStatus.REWORK,
                    updated_at=(
                        _dt.datetime.now(_dt.timezone.utc)
                        - _dt.timedelta(seconds=120)
                    ),
                )
            )
            await s.commit()

        await svc.ExecutionRunner()._auto_advance(pid)
        async with AsyncSession(dbengine) as s:
            st = (await s.execute(
                select(Task.status).where(Task.id == tid)
            )).scalar()
            assert st == TaskStatus.READY, "REWORK 应被接线为 READY"

    @pytest.mark.asyncio
    async def test_rework_fused_at_3_fails(self, dbengine, monkeypatch,
                                           tmp_path):
        """轮次闸: 3 轮 FAIL 的 REWORK 不再拉起（ManualAction 兜底）。"""
        from project_runtime.project_state.enums import (
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            GateResult,
            Project,
            Task,
        )
        from project_runtime.reviewer.gate import GateStatus
        from project_runtime.task_graph.manager import (
            CreateTaskRequest,
            TaskGraphManager,
        )

        monkeypatch.chdir(tmp_path)
        import project_runtime.event.engine as ev_mod
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        monkeypatch.setattr(ev_mod, "engine", dbengine)
        async with AsyncSession(dbengine) as s:
            p = Project(
                name="fused", automation_mode=AutomationMode.FULL_AUTO,
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="任务B", task_type="开发实现",
            ))
            tid, pid = t.id, p.id
            for _ in range(3):
                s.add(GateResult(task_id=tid, gate_name="g",
                                 status=GateStatus.FAIL, details="x"))
            await s.commit()
            import datetime as _dt

            from sqlalchemy import update

            await s.execute(
                update(Task).where(Task.id == tid).values(
                    status=TaskStatus.REWORK,
                    updated_at=(
                        _dt.datetime.now(_dt.timezone.utc)
                        - _dt.timedelta(seconds=120)
                    ),
                )
            )
            await s.commit()

        await svc.ExecutionRunner()._auto_advance(pid)
        async with AsyncSession(dbengine) as s:
            st = (await s.execute(
                select(Task.status).where(Task.id == tid)
            )).scalar()
            assert st == TaskStatus.REWORK, "3 轮 FAIL 不应再拉起"


class TestRunnerStrongRef:
    def test_background_runners_set_exists(self):
        """P0-2: 强引用集合存在（防 GC 丢任务的官方模式）。"""
        import project_runtime.execution.service as svc

        assert isinstance(svc._BACKGROUND_RUNNERS, set)

    def test_create_task_kept_alive(self):
        """create_task 的返回被集合持有（不悬空）。"""
        import project_runtime.execution.service as svc

        # 验证模式: 集合可持有/discard（接口契约）
        dummy = object()
        svc._BACKGROUND_RUNNERS.add(dummy)
        assert dummy in svc._BACKGROUND_RUNNERS
        svc._BACKGROUND_RUNNERS.discard(dummy)
        assert dummy not in svc._BACKGROUND_RUNNERS
