"""返修单v2 序6: 幻觉交付拦截 + 防死循环接线测试。

覆盖验收四专项:
- 幻觉交付拦截: 补建任务交 md 声称有 pptx 而实物不存在 → FAIL（reason 可行动）
- 真实物 → PASS
- 非补建任务零影响（自 PASS）
- Gate FAIL×3 → ManualAction 升级（防死循环熔断）
- 闭环二跳: 二次收口不再造任务（同名短路）
- 嵌套路径三形态（P4 已单测——此处冒烟）
"""

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
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bg.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


def _mk_task(task_name="交付补全-方案PPT", tags=("ppt",)):
    """构造 Gate 用的 Task stub（playbook_ref 带 delivery_tags）。"""
    import json

    from project_runtime.project_state.enums import TaskStatus

    class _T:
        id = 99
        name = task_name
        project_id = 1
        status = TaskStatus.EXECUTION_DONE
        playbook_ref = json.dumps({"delivery_tags": list(tags)})

    return _T()


class TestBackfillValidator:
    @pytest.mark.asyncio
    async def test_hallucinated_ppt_blocked(self, dbengine):
        """幻觉交付拦截: 只有 md（声称有 pptx）→ FAIL, reason 可行动。"""
        from project_runtime.reviewer.gate import (
            BackfillValidator,
            GateStatus,
        )

        class _A:
            name = "交付补全-方案PPT_output.md"

        r = await BackfillValidator().validate(_mk_task(), [_A()])
        assert r.status == GateStatus.FAIL
        assert ".pptx" in r.message and "slide_generator" in r.message

    @pytest.mark.asyncio
    async def test_real_pptx_passes(self, dbengine):
        from project_runtime.reviewer.gate import (
            BackfillValidator,
            GateStatus,
        )

        class _A1:
            name = "方案汇报.pptx"

        class _A2:
            name = "交付补全-方案PPT_output.md"

        r = await BackfillValidator().validate(_mk_task(), [_A1(), _A2()])
        assert r.status == GateStatus.PASS

    @pytest.mark.asyncio
    async def test_code_backfill_needs_readme(self, dbengine):
        from project_runtime.reviewer.gate import (
            BackfillValidator,
            GateStatus,
        )

        class _A:
            name = "main.py"

        r = await BackfillValidator().validate(
            _mk_task("交付补全-可运行代码", ("code",)), [_A()],
        )
        assert r.status == GateStatus.FAIL
        assert "README" in r.message

    @pytest.mark.asyncio
    async def test_normal_task_untouched(self, dbengine):
        """非补建任务零影响（无 tags 或名字不带前缀 → 自 PASS）。"""
        from project_runtime.reviewer.gate import (
            BackfillValidator,
            GateStatus,
        )

        class _A:
            name = "x_output.md"

        t1 = _mk_task("普通开发任务", ())
        r1 = await BackfillValidator().validate(t1, [_A()])
        assert r1.status == GateStatus.PASS

        t2 = _mk_task("接口开发")  # 有 tags 但名字非补建
        r2 = await BackfillValidator().validate(t2, [_A()])
        assert r2.status == GateStatus.PASS


class TestAntiDeadloop:
    @pytest.mark.asyncio
    async def test_gate_fail_x3_escalates_manual_action(self, dbengine):
        """防死循环熔断: Gate FAIL 第 3 轮 → ManualAction 升级（人工出口）。"""
        import json

        from project_runtime.project_state.enums import (
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            GateResult,
            ManualAction,
            Project,
            Task,
        )
        from project_runtime.reviewer.gate import GateCore, GateStatus

        async with AsyncSession(dbengine) as s:
            p = Project(name="fuse", automation_mode=AutomationMode.FULL_AUTO)
            s.add(p)
            await s.flush()
            from project_runtime.task_graph.manager import (
                CreateTaskRequest,
                TaskGraphManager,
            )

            mgr = TaskGraphManager(s)
            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="交付补全-方案PPT",
                task_type="交付打包",
            ))
            t.playbook_ref = json.dumps({"delivery_tags": ["ppt"]})
            tid = t.id
            # 预置 2 条历史 FAIL（本轮是第 3 次）
            for _ in range(2):
                s.add(GateResult(
                    task_id=tid, gate_name="gate_core",
                    status=GateStatus.FAIL, details="历史失败",
                ))
            await s.commit()
            # 任务置 EXECUTION_DONE（Gate 前置）
            from sqlalchemy import update

            await s.execute(
                update(Task).where(Task.id == tid)
                .values(status=TaskStatus.EXECUTION_DONE)
            )
            await s.commit()

        async with AsyncSession(dbengine) as s:
            core = GateCore(s)
            gr = await core.evaluate(tid)
            _status = gr.status  # commit 前取（过期属性触发 lazy load）
            await s.commit()
            assert _status == GateStatus.FAIL  # 本轮也 FAIL
        async with AsyncSession(dbengine) as s:
            ma = list(
                (
                    await s.execute(
                        select(ManualAction).where(
                            ManualAction.task_id == tid
                        )
                    )
                ).scalars().all()
            )
            assert len(ma) >= 1, "FAIL×3 应升级 ManualAction"
            payload = json.loads(ma[0].proposal_payload or "{}")
            assert "防死循环" in payload.get("reason", "")

    @pytest.mark.asyncio
    async def test_closeout_no_double_backfill(self, dbengine, monkeypatch,
                                               tmp_path):
        """闭环二跳短路: 补建任务已存在 → 二次收口不再造。"""
        from sqlalchemy import update

        from project_runtime.project_state.enums import (
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import Project, Task
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
                name="short", automation_mode=AutomationMode.FULL_AUTO,
                delivery_profile="SOFTWARE_DEV",
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            # 已有同名补建任务（首跳建的）
            await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="交付补全-方案PPT",
                task_type="交付打包",
            ))
            t0 = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="dev", task_type="开发实现",
            ))
            tid0, pid = t0.id, p.id
            await s.commit()
            await s.execute(
                update(Task).where(Task.id == tid0)
                .values(status=TaskStatus.ACCEPTED)
            )
            await s.commit()

        await svc._delivery_closeout_check(pid)
        await svc._delivery_closeout_check(pid)  # 二次收口
        async with AsyncSession(dbengine) as s:
            names = [
                n for (n,) in (
                    await s.execute(
                        select(Task.name).where(Task.project_id == pid)
                    )
                ).all()
            ]
            assert names.count("交付补全-方案PPT") == 1  # 不重复建


class TestNestedPath:
    @pytest.mark.asyncio
    async def test_three_forms(self, tmp_path, monkeypatch):
        """P4 冒烟: 嵌套路径三形态 + 越界拒（详测在 test_worker_tools）。"""
        import os

        monkeypatch.chdir(tmp_path)
        from project_runtime.worker.tools import FileRwProvider

        prov = FileRwProvider()
        r = await prov.invoke({
            "action": "write", "path": "1/8/pom.xml",
            "content": "x", "__ws_root": "data/tasks/1/8",
        })
        assert r["ok"] and os.path.exists(
            tmp_path / "data/tasks/1/8/pom.xml"
        )
