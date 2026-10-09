"""WP-034 Runtime Extension Framework tests — acceptance checklist.

Acceptance:
  [x] Observer 无阻断能力
  [x] Validator 无直接状态写权限
  [x] Middleware 返回新对象
  [x] Hook 可提出 Control Result
  [x] 权限不匹配时 Extension 调用被拒绝
Plus: plugin-backed runtime extension via stdio (bridge pattern), stable
point enforcement, hook plane failure isolation.
"""

import asyncio

import pytest

from project_runtime.extension.registry import (
    ExtensionRegistry,
    Permission,
)
from project_runtime.extension.runtime import (
    STABLE_POINTS,
    ControlResult,
    RuntimeExtensionError,
    RuntimeExtensionKind,
    RuntimeHookExecutor,
)


class Impl:
    """Minimal extension impl: returns a fixed envelope."""

    def __init__(self, result, permission=None, kind=None, payload=None):
        self._result = result
        self.permission = permission
        self.kind = kind
        self._payload = payload
        self.invoked = 0

    async def invoke(self, payload):
        self.invoked += 1
        if callable(self._result):
            return self._result(payload)
        return self._result


# ── 验收 §1: Observer 无阻断能力 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_observer_cannot_block():
    ex = RuntimeHookExecutor(ExtensionRegistry())
    # Observer trying to return a CONTROL value — must normalize to ACK
    ex.register_runtime(
        "worker.after_execute", "obs-1", RuntimeExtensionKind.OBSERVER,
        Impl({"result": "PAUSE"}),  # malicious/control-ish observer
    )
    outcome = await ex.fire_hook("worker.after_execute", {})
    assert outcome.results[0]["result"] == "ACK"  # forced to ACK
    assert outcome.control == ControlResult.CONTINUE  # no blocking power


# ── 验收 §2: Validator 无直接状态写权限 ──────────────────────────────


@pytest.mark.asyncio
async def test_validator_contract_only():
    ex = RuntimeHookExecutor(ExtensionRegistry())
    # Validator returning PASS/WARNING/FAIL is legal
    ex.register_runtime(
        "gate.validators", "v-1", RuntimeExtensionKind.VALIDATOR,
        Impl({"result": "FAIL", "detail": "artifact sha mismatch"}),
    )
    outcome = await ex.fire_hook("gate.validators", {})
    assert outcome.results[0]["result"] == "FAIL"

    # Validator trying to return a CONTROL value — contract violation
    ex2 = RuntimeHookExecutor(ExtensionRegistry())
    ex2.register_runtime(
        "gate.validators", "v-2", RuntimeExtensionKind.VALIDATOR,
        Impl({"result": "PAUSE"}),  # validators may not pause
    )
    outcome2 = await ex2.fire_hook("gate.validators", {})
    assert outcome2.results[0]["result"] == "EXTENSION_ERROR"
    # And crucially: no control influence
    assert outcome2.control == ControlResult.CONTINUE


# ── 验收 §3: Middleware 返回新对象 ────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_returns_new_object():
    ex = RuntimeHookExecutor(ExtensionRegistry())

    class MW(Impl):
        async def invoke(self, payload):
            await super().invoke(payload)
            # Middleware returns a NEW payload object (never mutates input)
            new = dict(payload)
            new["enriched"] = True
            return {"result": "ACK", "object": new}

    mw = MW({"result": "ACK"})
    ex.register_runtime("context.middleware", "mw-1", RuntimeExtensionKind.MIDDLEWARE, mw)
    payload = {"task_id": 1}
    outcome = await ex.fire_hook("context.middleware", payload)
    assert mw.invoked == 1
    assert outcome.results[0]["result"] == "ACK"
    assert payload == {"task_id": 1}  # original untouched


# ── 验收 §4: Hook 可提出 Control Result ───────────────────────────────


@pytest.mark.asyncio
async def test_hook_proposes_control():
    ex = RuntimeHookExecutor(ExtensionRegistry())
    ex.register_runtime(
        "worker.before_execute", "h-1", RuntimeExtensionKind.HOOK,
        Impl({"result": "NEED_USER_INPUT", "detail": "missing api key"}),
    )
    ex.register_runtime(
        "worker.before_execute", "h-2", RuntimeExtensionKind.HOOK,
        Impl({"result": "CONTINUE"}),
    )
    outcome = await ex.fire_hook("worker.before_execute", {"task_id": 1})
    # NEED_USER_INPUT wins over CONTINUE (aggregation priority)
    assert outcome.control == ControlResult.NEED_USER_INPUT

    # PROPOSAL outcome collection
    ex2 = RuntimeHookExecutor(ExtensionRegistry())
    ex2.register_runtime(
        "scheduler.before_dispatch", "h-3", RuntimeExtensionKind.HOOK,
        Impl({"result": "PROPOSAL", "detail": {"suggest": "defer task"}}),
    )
    outcome2 = await ex2.fire_hook("scheduler.before_dispatch", {})
    assert outcome2.control == ControlResult.PROPOSAL
    assert len(outcome2.proposals) == 1


# ── 验收 §5: 权限不匹配时 Extension 调用被拒绝 ─────────────────────────


@pytest.mark.asyncio
async def test_permission_mismatch_rejected():
    ex = RuntimeHookExecutor(ExtensionRegistry())
    # A HOOK-kind extension declared with READ_ONLY permission → rejected
    impl = Impl({"result": "PAUSE"}, permission=Permission.READ_ONLY)
    ex.register_runtime(
        "worker.before_execute", "h-bad", RuntimeExtensionKind.HOOK, impl
    )
    outcome = await ex.fire_hook("worker.before_execute", {})
    assert outcome.results[0]["result"] == "REJECTED_PERMISSION"
    assert impl.invoked == 0  # never actually invoked


def test_permission_requirement_matrix():
    from project_runtime.extension.runtime import KIND_PERMISSION

    assert KIND_PERMISSION[RuntimeExtensionKind.OBSERVER] == Permission.READ_ONLY
    assert KIND_PERMISSION[RuntimeExtensionKind.VALIDATOR] == Permission.VALIDATE
    assert KIND_PERMISSION[RuntimeExtensionKind.MIDDLEWARE] == Permission.ENRICH
    assert KIND_PERMISSION[RuntimeExtensionKind.HOOK] == Permission.CONTROL
    assert KIND_PERMISSION[RuntimeExtensionKind.POLICY] == Permission.CONTROL
    # DIRECT_STATE_MUTATE structurally absent (D16 + WP-034 prohibition)
    assert not hasattr(Permission, "DIRECT_STATE_MUTATE")


# ── 稳定点 ID 纪律（AGENTS.md 约束 5）──────────────────────────────────


def test_stable_points_only():
    ex = RuntimeHookExecutor(ExtensionRegistry())
    with pytest.raises(RuntimeExtensionError, match="stable ids only"):
        ex.register_runtime(
            "my.custom.point", "x", RuntimeExtensionKind.HOOK, Impl({"result": "CONTINUE"})
        )
    assert "worker.before_execute" in STABLE_POINTS
    assert "gate.validators" in STABLE_POINTS


# ── 失败隔离：单个扩展炸不阻断 hook 链 ────────────────────────────────


@pytest.mark.asyncio
async def test_one_failing_extension_does_not_break_chain():
    ex = RuntimeHookExecutor(ExtensionRegistry())

    class Boom(Impl):
        async def invoke(self, payload):
            raise RuntimeError("extension crashed")

    ok_impl = Impl({"result": "ACK"})
    boom_impl = Boom({"result": "ACK"})
    ex.register_runtime(
        "worker.after_execute", "ok-1", RuntimeExtensionKind.OBSERVER, ok_impl
    )
    ex.register_runtime(
        "worker.after_execute", "boom", RuntimeExtensionKind.OBSERVER, boom_impl
    )
    outcome = await ex.fire_hook("worker.after_execute", {})
    kinds = {r["extension_id"]: r["result"] for r in outcome.results}
    assert kinds["ok-1"] == "ACK"
    assert kinds["boom"] == "EXTENSION_ERROR"
    assert outcome.control == ControlResult.CONTINUE  # chain survived


# ── 真实链路: ExecutionRunner 的 hook 触发（接线实证）─────────────────


@pytest.mark.asyncio
async def test_runner_fires_worker_hooks(tmp_path, monkeypatch):
    """worker.before/after_execute 在真实执行链上被触发（WP-034 接线）."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    import project_runtime.event.engine as evmod
    import project_runtime.event.models  # noqa
    import project_runtime.execution.models  # noqa
    import project_runtime.execution.service as svc
    import project_runtime.project_state.models  # noqa
    from project_runtime.project_state.base import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hooks.db")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(svc, "engine", eng)
    # 联网制作 skill 禁用（测试离线——fabricator 真联网会卡 30s×N）
    monkeypatch.setattr(svc, "_fabricate_searcher", lambda: False)
    monkeypatch.setattr(evmod, "engine", eng)

    from project_runtime.project_state.enums import AutomationMode, TaskStatus
    from project_runtime.project_state.models import Project, Task
    from project_runtime.task_graph.manager import TaskGraphManager

    async with AsyncSession(eng) as s:
        p = Project(name="hooks", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(Task(project_id=p.id, name="hooked"))
        await mgr.promote_to_ready(t.id)
        tid = t.id
        pid = p.id
        await s.commit()

    async def fake_llm(self, wp):
        return "# done"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )

    # Register observers on both points via the runner's shared executor
    before = Impl({"result": "ACK"})
    after = Impl({"result": "ACK"})
    svc.hook_executor.register_runtime(
        "worker.before_execute", "test.obs", RuntimeExtensionKind.OBSERVER, before
    )
    svc.hook_executor.register_runtime(
        "worker.after_execute", "test.obs2", RuntimeExtensionKind.OBSERVER, after
    )

    await svc.ExecutionService().submit(pid, tid)
    for _ in range(100):
        async with AsyncSession(eng) as s:
            from sqlalchemy import select as _sel

            st = (await s.execute(_sel(Task.status).where(Task.id == tid))).scalar_one()
        if st == TaskStatus.ACCEPTED:
            break
        await asyncio.sleep(0.1)

    assert before.invoked >= 1  # before_execute fired in the real chain
    assert after.invoked >= 1   # after_execute fired too


@pytest.mark.asyncio
async def test_before_execute_pause_advisory(tmp_path, monkeypatch):
    """Hook PAUSE → 任务本轮不派发（advisory 控制，Runtime 存活）."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    import project_runtime.event.engine as evmod
    import project_runtime.event.models  # noqa
    import project_runtime.execution.models  # noqa
    import project_runtime.execution.service as svc
    import project_runtime.project_state.models  # noqa
    from project_runtime.project_state.base import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/pause.db")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(svc, "engine", eng)
    monkeypatch.setattr(svc, "_fabricate_searcher", lambda: False)
    monkeypatch.setattr(evmod, "engine", eng)

    from project_runtime.project_state.enums import AutomationMode, TaskStatus
    from project_runtime.project_state.models import Project, Task
    from project_runtime.task_graph.manager import TaskGraphManager

    async with AsyncSession(eng) as s:
        p = Project(name="pause", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(Task(project_id=p.id, name="paused-task"))
        await mgr.promote_to_ready(t.id)
        tid = t.id
        pid = p.id
        await s.commit()

    async def fake_llm(self, wp):
        return "# done"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )

    # A HOOK extension that advises PAUSE
    svc.hook_executor.register_runtime(
        "worker.before_execute", "pauser", RuntimeExtensionKind.HOOK,
        Impl({"result": "PAUSE"}),
    )
    try:
        await svc.ExecutionService().submit(pid, tid)
        await asyncio.sleep(1.5)
        async with AsyncSession(eng) as s:
            from sqlalchemy import select as _sel

            st = (await s.execute(_sel(Task.status).where(Task.id == tid))).scalar_one()
        # Task NOT executed (advisory pause honored for this round)
        assert st == TaskStatus.READY
    finally:
        svc.hook_executor.unregister_runtime("worker.before_execute", "pauser")
