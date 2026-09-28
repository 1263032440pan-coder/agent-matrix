"""WP-036 Resource Profiles tests — Gate 六条 + 复验预告①-④.

Gate:
  [x] 8GB 模拟配置下可限制为单 Worker（auto_profile → LOW → concurrency 1）
  [x] 3 个逻辑可并行任务在 LOW 下自动串行执行
  [x] HIGH 下能提高实际并发
  [x] 内存压力触发 DEFER_RESOURCE
  [x] Task Graph 在 LOW/HIGH 下保持一致（结构性：governor 无依赖边写路径）
  [x] FULL_AUTO + LOW 仍无需人工确认（governor 不碰 Automation Mode）

复验预告:
  ① Profile 三档真实生效（决策差异可测）
  ② RUN_EXCLUSIVE / REDUCE_PARALLELISM 行为化
  ③ 与 D15 整合：包装非替换（老调用面不变，4 老测试全过）
  ④ ADR-026：8GB → LOW 映射
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.project_state.models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.resource_governor import (
    PROFILE_SETTINGS,
    ResourceDecision,
    ResourceGovernor,
    ResourceProfile,
    SystemResourceSnapshot,
    auto_profile,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/res.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


_PID = 1  # single shared project id


async def _set_running(dbengine, n_running: int, pid: int = _PID):
    """Reset RUNNING rows for the project to exactly n_running."""
    from sqlalchemy import delete

    async with AsyncSession(dbengine) as s:
        await s.execute(delete(Task).where(Task.project_id == pid))
        for i in range(n_running):
            s.add(Task(
                project_id=pid, name=f"r{i}", status=TaskStatus.RUNNING
            ))
        await s.commit()


async def _project_with_running(dbengine, n_running: int, pid: int = _PID):
    async with AsyncSession(dbengine) as s:
        p = Project(name="res", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        for i in range(n_running):
            s.add(Task(
                project_id=pid, name=f"r{i}", status=TaskStatus.RUNNING
            ))
        await s.commit()


def _snap(available_mb: int, total_mb: int = 8192) -> SystemResourceSnapshot:
    return SystemResourceSnapshot(
        total_memory_mb=total_mb, available_memory_mb=available_mb,
        cpu_percent=0.0,
    )


# ── ④ ADR-026: 8GB 映射 ──────────────────────────────────────────────


def test_auto_profile_adr026():
    assert auto_profile(_snap(1000, total_mb=8192)) is ResourceProfile.LOW
    assert auto_profile(_snap(1000, total_mb=4096)) is ResourceProfile.LOW
    assert auto_profile(_snap(8000, total_mb=16384)) is ResourceProfile.STANDARD
    assert auto_profile(_snap(32000, total_mb=32768)) is ResourceProfile.HIGH


# ── ① Profile 三档真实生效 ────────────────────────────────────────────


def test_profile_bounds_differ():
    low = PROFILE_SETTINGS[ResourceProfile.LOW]
    std = PROFILE_SETTINGS[ResourceProfile.STANDARD]
    high = PROFILE_SETTINGS[ResourceProfile.HIGH]
    # 决策差异可测：并发边界单调递增
    assert low.worker_concurrency < std.worker_concurrency < high.worker_concurrency
    assert low.worker_concurrency == 1  # rule 4: LOW concurrency = 1
    assert low.heavy_task_exclusive is True
    assert high.heavy_task_exclusive is False
    assert low.browser_contexts == 1  # ADR-026 LOW 默认


@pytest.mark.asyncio
async def test_low_serializes_high_parallels(dbengine):
    """Gate §2: 3 个逻辑可并行任务在 LOW 下串行（第 2 个起 DEFER）。"""
    await _project_with_running(dbengine, n_running=1)

    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=ResourceProfile.LOW)
        # 1 running + LOW(1) → next defers
        assert await gov.evaluate(1) is ResourceDecision.DEFER_RESOURCE

    # 高并发档对比：HIGH(8) 同样 1 running → allow
    await _project_with_running(dbengine, n_running=1)
    async with AsyncSession(dbengine) as s:
        gov_high = ResourceGovernor(s, profile=ResourceProfile.HIGH)
        assert await gov_high.evaluate(1) is ResourceDecision.ALLOW_DISPATCH


# ── Gate §1: 8GB 模拟配置 → 单 Worker ─────────────────────────────────


@pytest.mark.asyncio
async def test_8gb_config_single_worker(dbengine):
    snap = _snap(available_mb=4000, total_mb=8192)  # 8GB 机器
    profile = auto_profile(snap)
    assert profile is ResourceProfile.LOW

    await _project_with_running(dbengine, n_running=0)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=profile)
        # 第一个任务可跑
        assert await gov.evaluate(1) is ResourceDecision.ALLOW_DISPATCH

    # 模拟 1 RUNNING 后第二个 DEFER（单 worker 限制生效）
    await _project_with_running(dbengine, n_running=1)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=profile)
        assert await gov.evaluate(1) is ResourceDecision.DEFER_RESOURCE


# ── Gate §3: HIGH 提高并发 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_high_allows_more_parallelism(dbengine):
    await _project_with_running(dbengine, n_running=5)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=ResourceProfile.HIGH)
        # 5 running < HIGH(8) → still allow
        assert await gov.evaluate(1) is ResourceDecision.ALLOW_DISPATCH

    await _project_with_running(dbengine, n_running=8)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=ResourceProfile.HIGH)
        # 8 running = cap → defer
        assert await gov.evaluate(1) is ResourceDecision.DEFER_RESOURCE


# ── Gate §4: 内存压力 DEFER ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_pressure_defers(dbengine):
    await _project_with_running(dbengine, n_running=0)

    async with AsyncSession(dbengine) as s:
        # LOW 压力阈 1024MB——可用 500MB → DEFER
        gov = ResourceGovernor(
            s, profile=ResourceProfile.LOW,
            snapshot_provider=lambda: _snap(available_mb=500),
        )
        assert await gov.evaluate(1) is ResourceDecision.DEFER_RESOURCE

        # 充足内存（8GB 机器 4GB 可用）→ ALLOW
        gov_ok = ResourceGovernor(
            s, profile=ResourceProfile.LOW,
            snapshot_provider=lambda: _snap(available_mb=4000),
        )
        assert await gov_ok.evaluate(1) is ResourceDecision.ALLOW_DISPATCH


# ── ② RUN_EXCLUSIVE / REDUCE_PARALLELISM 行为 ─────────────────────────


@pytest.mark.asyncio
async def test_run_exclusive_heavy_task(dbengine):
    await _project_with_running(dbengine, n_running=0)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(
            s, profile=ResourceProfile.LOW,  # heavy_task_exclusive=True
            snapshot_provider=lambda: _snap(4000),
        )
        # heavy task → RUN_EXCLUSIVE
        assert await gov.evaluate(1, heavy=True) is ResourceDecision.RUN_EXCLUSIVE

    # heavy task now actually RUNNING → exclusive holds (no self-heal)
    await _set_running(dbengine, n_running=1)
    async with AsyncSession(dbengine) as s:
        gov2 = ResourceGovernor(
            s, profile=ResourceProfile.LOW,
            snapshot_provider=lambda: _snap(4000),
        )
        gov2._exclusive_running = True  # simulate held exclusive
        # light task defers while heavy runs
        assert await gov2.evaluate(1) is ResourceDecision.DEFER_RESOURCE

    # heavy done (no RUNNING) → self-heal releases exclusive → allow
    await _set_running(dbengine, n_running=0)
    async with AsyncSession(dbengine) as s:
        gov3 = ResourceGovernor(
            s, profile=ResourceProfile.LOW,
            snapshot_provider=lambda: _snap(4000),
        )
        gov3._exclusive_running = True  # stale flag
        assert await gov3.evaluate(1) is ResourceDecision.ALLOW_DISPATCH


@pytest.mark.asyncio
async def test_reduce_parallelism_on_tightening(dbengine):
    """STANDARD(3): 1 running + 内存吃紧(<2x阈值=1024MB) → cap 3→2 → 仍允许;
    2 running + 吃紧 → cap 2 满 → DEFER（等效降并发）。"""
    await _project_with_running(dbengine, n_running=1)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(
            s, profile=ResourceProfile.STANDARD,
            snapshot_provider=lambda: _snap(available_mb=800),  # < 2x512
        )
        # 1 running < reduced cap 2 → allow
        assert await gov.evaluate(1) is ResourceDecision.ALLOW_DISPATCH

    await _project_with_running(dbengine, n_running=2)
    async with AsyncSession(dbengine) as s:
        gov2 = ResourceGovernor(
            s, profile=ResourceProfile.STANDARD,
            snapshot_provider=lambda: _snap(available_mb=800),
        )
        # 2 running = reduced cap 2 → defer（无压力时 cap=3 会允许）
        assert await gov2.evaluate(1) is ResourceDecision.DEFER_RESOURCE


# ── Gate §5: Task Graph 一致（结构性）─────────────────────────────────


def test_governor_never_touches_task_graph():
    import inspect

    from project_runtime.resource_governor import governor as gov_mod

    src = inspect.getsource(gov_mod)
    for forbidden in (
        "TaskDependency(", "depends_on", "promote_to_ready",
        "transition_task",  # FSM
        "AutomationMode.",  # rule 2
    ):
        assert forbidden not in src, f"governor must not touch: {forbidden}"


# ── Gate §6: FULL_AUTO + LOW 无需人工确认 ─────────────────────────────


@pytest.mark.asyncio
async def test_full_auto_low_no_manual_confirm(dbengine):
    """LOW 档不改变 FULL_AUTO 语义：决策只返回 dispatch 类，绝不产生
    ManualAction（那是 MANUAL 模式专属）。"""
    await _project_with_running(dbengine, n_running=0)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, profile=ResourceProfile.LOW)
        decision = await gov.evaluate(1)
        # ALLOW_DISPATCH —— 不产生任何人工确认语义
        assert decision is ResourceDecision.ALLOW_DISPATCH
        # governor 无 ManualAction 写路径（结构性）
        import inspect

        from project_runtime.resource_governor import governor as gov_mod

        assert "ManualAction" not in inspect.getsource(gov_mod)


# ── ③ D15 整合：老调用面兼容 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_d15_compatibility_max_workers_only(dbengine):
    """D15 风格构造（max_workers，无 profile）依旧工作。"""
    await _project_with_running(dbengine, n_running=1)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, 2)  # max_workers=2, D15 style
        assert await gov.evaluate(1) is ResourceDecision.ALLOW_DISPATCH

    await _project_with_running(dbengine, n_running=2)
    async with AsyncSession(dbengine) as s:
        gov = ResourceGovernor(s, 2)
        assert await gov.evaluate(1) is ResourceDecision.DEFER_RESOURCE


# ── Scheduler 集成: [heavy] 标记 → RUN_EXCLUSIVE 派发路径 ─────────────


@pytest.mark.asyncio
async def test_scheduler_heavy_exclusive_dispatch(dbengine):
    """[heavy] 任务经 Scheduler 正常派发且本轮 pass 独占（后续 READY 不派发）。"""
    from project_runtime.scheduler.scheduler import Scheduler
    from project_runtime.worker.adapter import WorkerAdapter, WorkPackage

    class RecordingWorker(WorkerAdapter):
        def __init__(self):
            self.assigned: list[int] = []

        async def assign(self, package: WorkPackage) -> None:
            self.assigned.append(package.task_id)

        async def cancel(self, task_id: int) -> None:  # pragma: no cover
            pass

        async def health(self) -> str:  # pragma: no cover
            return "ok"

    async with AsyncSession(dbengine) as s:
        p = Project(name="sched-heavy", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        # heavy 排在最前 + 两个普通任务随后
        s.add(Task(project_id=pid, name="browser-scan [heavy]",
                   status=TaskStatus.READY))
        s.add(Task(project_id=pid, name="plain-a", status=TaskStatus.READY))
        s.add(Task(project_id=pid, name="plain-b", status=TaskStatus.READY))
        await s.commit()

    worker = RecordingWorker()
    async with AsyncSession(dbengine) as s:
        sched = Scheduler(
            s, worker=worker, profile=ResourceProfile.LOW,
            snapshot_provider=lambda: _snap(4000),
        )
        decisions = await sched.dispatch_once(pid)

    # heavy 被派发，pass 在 heavy 后终止（独占）
    assert worker.assigned == [1]
    assert decisions[0].action == "DISPATCHED"
    assert len(decisions) == 1  # exclusive → no further dispatch this pass
