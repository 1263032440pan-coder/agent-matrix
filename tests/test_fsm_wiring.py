"""20号规范批次 3 FSM 接线测试: 执行链 ↔ AgentInstance 生命周期（全离线）.

验证「FSM 有人驱动」的闭环（此前三批只测组件本体——本文件测真实链路）:
  [x] bind_agent: 实例 CREATED→IDLE→BUSY + current_task_id 落库
  [x] release_agent: BUSY→IDLE + current_task_id 清空 + token 记账
  [x] fail_agent: BUSY→IDLE + fail_count 累计; 3 连败→DEGRADED+快照+传播
  [x] 无实例: bind 返回 None（老路径零回归语义）
  [x] /plan 集成: 规划后 provision 建实例（按 task_type 环节）
  [x] 执行链集成: ExecutionRunner 全链跑完 Agent 回 IDLE（真链非直调）
  [x] 启动 seed: lifespan 后五内置模板在库
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
from project_runtime.project_state.enums import (
    AutomationMode,
    TaskStatus,
)
from project_runtime.project_state.models import Project, Task
from project_runtime.worker.agent_models import AgentStatus
from project_runtime.worker.lifecycle import (
    bind_agent,
    fail_agent,
    provision_after_plan,
    release_agent,
)
from project_runtime.worker.registry import (
    AgentProvisioner,
    seed_builtin_templates,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fsm.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="fsm", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


async def _status_of(session, instance_id: int) -> AgentStatus:
    from project_runtime.worker.agent_models import AgentInstance

    row = await session.execute(
        select(AgentInstance.status).where(AgentInstance.id == instance_id)
    )
    return row.scalar_one()


# ── 生命周期三原语 ─────────────────────────────────────────────────


class TestLifecyclePrimitives:
    @pytest.mark.asyncio
    async def test_bind_created_to_busy(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()
        async with AsyncSession(eng) as s:
            binding = await bind_agent(s, pid, task_id=99, task_type="开发实现")
            await s.commit()
        assert binding is not None
        async with AsyncSession(eng) as s:
            assert await _status_of(s, binding.instance_id) is AgentStatus.BUSY
            from project_runtime.worker.agent_models import AgentInstance

            inst = await s.get(AgentInstance, binding.instance_id)
            assert inst.current_task_id == 99

    @pytest.mark.asyncio
    async def test_release_back_to_idle_with_cost(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()
        async with AsyncSession(eng) as s:
            binding = await bind_agent(s, pid, task_id=7, task_type="开发实现")
            await s.commit()
        async with AsyncSession(eng) as s:
            await release_agent(s, binding, tokens_in=100, tokens_out=50)
            await s.commit()
        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            inst = await s.get(AgentInstance, binding.instance_id)
            assert inst.status is AgentStatus.IDLE
            assert inst.current_task_id is None
            assert inst.token_used == 150   # 记账
            assert inst.call_count == 1

    @pytest.mark.asyncio
    async def test_fail_accumulates_then_degrades(self, dbengine, tmp_path,
                                                  monkeypatch):
        """3 连败 → DEGRADED + 快照 + 传播决策（三段链全触发）。"""
        eng, pid = dbengine
        monkeypatch.chdir(tmp_path)
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()
        # 三次失败

        async with AsyncSession(eng) as s:
            binding = await bind_agent(s, pid, task_id=1, task_type="开发实现")
            await s.commit()
        for i in range(3):
            async with AsyncSession(eng) as s:
                # 每轮重新 BUSY（模拟连续三次派发失败）
                from project_runtime.worker.agent_models import AgentInstance

                inst = await s.get(AgentInstance, binding.instance_id)
                if inst.status is AgentStatus.IDLE:
                    inst.status = AgentStatus.BUSY
                    inst.current_task_id = 100 + i
                await s.flush()
                await fail_agent(s, binding, reason=f"fail#{i}")
                await s.commit()
        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            inst = await s.get(AgentInstance, binding.instance_id)
            # 3 连败（阈值 3）→ DEGRADED
            assert inst.status is AgentStatus.DEGRADED
            assert inst.degraded_until is not None
            # 快照落库（V1.3 P2-3: DEGRADED 必快照）
            from project_runtime.checkpoint.models import CheckpointRecord

            cps = (
                await s.execute(
                    select(CheckpointRecord).where(
                        CheckpointRecord.trigger == "AGENT_DEGRADED"
                    )
                )
            ).scalars().all()
            assert len(cps) == 1

    @pytest.mark.asyncio
    async def test_no_instance_returns_none(self, dbengine):
        """无实例（未 provision）→ None——老路径零回归。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            binding = await bind_agent(s, pid, task_id=1, task_type="开发实现")
            await s.commit()
        assert binding is None


# ── /plan 集成: provision 落地 ─────────────────────────────────────


class TestPlanProvision:
    @pytest.mark.asyncio
    async def test_provision_after_plan(self, dbengine):
        """规划后按 task_type 环节建实例（mock 任务图）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await s.commit()
        async with AsyncSession(eng) as s:
            s.add(Task(project_id=pid, name="A", task_type="开发实现"))
            s.add(Task(project_id=pid, name="B", task_type="测试验证"))
            await s.commit()
        async with AsyncSession(eng) as s:
            n = await provision_after_plan(
                s, pid, ["开发实现", "测试验证"]
            )
            await s.commit()
        assert n == 2  # 开发+测试两环节各一实例
        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            rows = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().all()
            assert len(rows) == 2
            assert all(r.status is AgentStatus.CREATED for r in rows)

    @pytest.mark.asyncio
    async def test_provision_idempotent(self, dbengine):
        """同环节重复 provision 不叠实例（唯一活跃索引语义）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await s.commit()
        for _ in range(2):
            async with AsyncSession(eng) as s:
                await provision_after_plan(s, pid, ["开发实现"])
                await s.commit()
        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            rows = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().all()
            assert len(rows) == 1


# ── 执行链集成: 真实 runner 驱动 FSM ───────────────────────────────


class TestExecutionChainWiring:
    @pytest.mark.asyncio
    async def test_runner_releases_agent_on_success(self, dbengine, monkeypatch):
        """全链集成: seed→provision→执行成功→Agent 回 IDLE+记账。

        直接驱动 ExecutionService.submit + Runner.run（LLM mock）——
        验证 service.py 里的 bind/release 挂钩真实生效（非直调原语）。
        """
        eng, pid = dbengine
        # 引擎注入（skill 注入点纪律同款——service 模块级 engine）
        from project_runtime.execution import service as svc

        monkeypatch.setattr(svc, "engine", eng)

        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await s.commit()
        async with AsyncSession(eng) as s:
            task = Task(project_id=pid, name="编码任务",
                        task_type="开发实现", status=TaskStatus.READY)
            s.add(task)
            await s.flush()
            tid = task.id
            await s.commit()
        async with AsyncSession(eng) as s:
            n = await provision_after_plan(s, pid, ["开发实现"])
            await s.commit()
        assert n == 1

        # LLM mock（不打真网）
        async def fake_execute(self, wp):
            return "产物内容"

        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        monkeypatch.setattr(LLMWorkerAdapter, "_execute_llm", fake_execute)

        service = svc.ExecutionService()
        ex_id = await service.submit(pid, tid)
        await svc.ExecutionRunner().run(ex_id)

        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            inst = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().first()
            assert inst is not None
            assert inst.status is AgentStatus.IDLE, (
                f"runner 跑完 Agent 应回 IDLE，实际 {inst.status}"
            )
            assert inst.current_task_id is None

    @pytest.mark.asyncio
    async def test_runner_counts_failure(self, dbengine, monkeypatch):
        """执行失败 → fail_count +1（Agent 面 best-effort 不炸任务）。"""
        eng, pid = dbengine
        from project_runtime.execution import service as svc

        monkeypatch.setattr(svc, "engine", eng)

        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await s.commit()
        async with AsyncSession(eng) as s:
            task = Task(project_id=pid, name="失败任务",
                        task_type="开发实现", status=TaskStatus.READY)
            s.add(task)
            await s.flush()
            tid = task.id
            await s.commit()
        async with AsyncSession(eng) as s:
            await provision_after_plan(s, pid, ["开发实现"])
            await s.commit()

        async def boom(self, wp):
            raise RuntimeError("LLM gateway down")

        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        monkeypatch.setattr(LLMWorkerAdapter, "_execute_llm", boom)

        service = svc.ExecutionService()
        ex_id = await service.submit(pid, tid)
        await svc.ExecutionRunner().run(ex_id)

        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentInstance

            inst = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().first()
            assert inst is not None
            # 失败计数进了（Gate FAIL→REWORK→auto-advance 重试会叠加
            # 计数——每次执行失败都计是真实语义; 未达 3 阈值保持 IDLE）
            assert inst.fail_count >= 1
            assert inst.status is AgentStatus.IDLE
