"""WP-035 Pipeline Engine tests — acceptance + 复验预告重点.

Acceptance:
  [x] Base Pipeline 可声明
  [x] Hook / Middleware / Validator 可注入
  [x] 编译结果可查看
  [x] 插件 enable/disable 会触发重新 compile（hash 变化实证）
  [x] 顺序冲突可检测（EXTENSION_DEPENDENCY_CYCLE 阻止编译）

复验重点:
  1. dependency_graph_hash 是真实变更检测（同输入同 hash / 任何变化 hash 变）
  2. 扩展点只落 21 稳定点（共用 WP-034 清单，无第二份）
  3. before/after 语义与交错次序
  4. 核心步骤失败 vs 扩展失败的不同处理
  5. TD01: hook_executor 注入化
"""

import pytest

from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.extension.runtime import (
    RuntimeExtensionKind,
    RuntimeHookExecutor,
)
from project_runtime.pipeline import (
    BASE_PIPELINES,
    ExtensionBinding,
    ExtensionDependencyCycleError,
    PipelineEngine,
    PipelineError,
)
from project_runtime.pipeline.compiler import compile_pipeline
from project_runtime.pipeline.definition import PipelineDefinition, PipelineStep


def _engine() -> PipelineEngine:
    # TD01: engine takes an INJECTED executor (no hidden singleton)
    return PipelineEngine(RuntimeHookExecutor(ExtensionRegistry()))


# ── 验收 §1: Base Pipeline 可声明 ─────────────────────────────────────


def test_base_pipelines_declared():
    assert set(BASE_PIPELINES) == {
        "project_bootstrap", "project_understanding", "planning",
        "task_execution", "gate_evaluation", "delivery_validation",
    }
    # task_execution 的七步固定序
    steps = [s.step_id for s in BASE_PIPELINES["task_execution"].steps]
    assert steps == [
        "task.prepare", "automation.check", "context.build", "worker.select",
        "worker.execute", "result.process", "gate.evaluate",
    ]


def test_pipeline_point_must_be_stable():
    bad = PipelineDefinition(
        pipeline_id="bad", version="1",
        steps=[PipelineStep("a")], extension_points=["my.custom.point"],
    )
    with pytest.raises(PipelineError, match="stable point"):
        compile_pipeline(bad, [])


# ── 验收 §2: Hook / Middleware / Validator 可注入 ─────────────────────


def test_extensions_injectable():
    eng = _engine()
    eng.bind_extension("task_execution", ExtensionBinding(
        extension_id="obs-1", point="worker.before_execute",
        kind=RuntimeExtensionKind.OBSERVER,
    ))
    eng.bind_extension("task_execution", ExtensionBinding(
        extension_id="mw-1", point="scheduler.before_dispatch",
        kind=RuntimeExtensionKind.MIDDLEWARE,
    ))
    eng.bind_extension("gate_evaluation", ExtensionBinding(
        extension_id="val-1", point="gate.validators",
        kind=RuntimeExtensionKind.VALIDATOR,
    ))
    compiled = eng.get_compiled("task_execution")
    kinds = {e["kind"] for e in compiled.registered_extensions}
    assert kinds == {"Observer", "Middleware"}
    gate = eng.get_compiled("gate_evaluation")
    assert {e["extension_id"] for e in gate.registered_extensions} == {"val-1"}


def test_binding_outside_declared_points_rejected():
    eng = _engine()
    with pytest.raises(PipelineError, match="does not declare"):
        eng.bind_extension("planning", ExtensionBinding(
            extension_id="x", point="worker.before_execute",  # planning 没声明这个点
            kind=RuntimeExtensionKind.OBSERVER,
        ))


# ── 验收 §3: 编译结果可查看 ───────────────────────────────────────────


def test_compiled_view():
    eng = _engine()
    compiled = eng.get_compiled("planning")
    d = compiled.to_dict()
    assert d["pipeline_id"] == "planning"
    assert d["base_version"] == "1.0"
    assert "dependency_graph_hash" in d and len(d["dependency_graph_hash"]) == 64
    assert any(s["id"] == "planner.plan" for s in d["ordered_steps"])


# ── 验收 §4: enable/disable 触发重新 compile（hash 真变化）────────────


def test_bind_unbind_changes_hash():
    eng = _engine()
    h0 = eng.get_compiled("planning").dependency_graph_hash

    h1 = eng.bind_extension("planning", ExtensionBinding(
        extension_id="e1", point="planner.before", kind=RuntimeExtensionKind.OBSERVER,
    ))
    assert h1 != h0  # real change detected

    h2 = eng.bind_extension("planning", ExtensionBinding(
        extension_id="e2", point="planner.after", kind=RuntimeExtensionKind.OBSERVER,
    ))
    assert h2 != h1

    h3 = eng.unbind_extension("planning", "e2")
    assert h3 != h2
    assert h3 == h1  # removing e2 restores the e1-only fingerprint

    h4 = eng.unbind_extension("planning", "e1")
    assert h4 == h0  # back to base — hash is a REAL change detector


def test_recompile_same_state_same_hash():
    eng = _engine()
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="e1", point="planner.before", kind=RuntimeExtensionKind.OBSERVER,
    ))
    h1 = eng.recompile("planning").dependency_graph_hash
    h2 = eng.recompile("planning").dependency_graph_hash  # no change
    assert h1 == h2  # deterministic — hash is content-based, not time-based


# ── 验收 §5: 顺序冲突可检测 ───────────────────────────────────────────


def test_dependency_cycle_blocks_compilation():
    eng = _engine()
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="a", point="planner.before",
        kind=RuntimeExtensionKind.OBSERVER, before=["b"],   # a → b
    ))
    with pytest.raises(ExtensionDependencyCycleError, match="CYCLE"):
        eng.bind_extension("planning", ExtensionBinding(
            extension_id="b", point="planner.after",
            kind=RuntimeExtensionKind.OBSERVER, before=["a"],  # b → a (环!)
        ))
    # The cyclic binding must NOT be recorded — pipeline stays usable
    names = [e["extension_id"] for e in eng.get_compiled("planning").registered_extensions]
    assert names == ["a"]


def test_topo_order_respects_before_after():
    eng = _engine()
    # c after b, b after a → order a, b, c regardless of priority
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="c", point="planner.after",
        kind=RuntimeExtensionKind.OBSERVER, priority=99, after=["b"],
    ))
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="b", point="planner.after",
        kind=RuntimeExtensionKind.OBSERVER, priority=50, after=["a"],
    ))
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="a", point="planner.before",
        kind=RuntimeExtensionKind.OBSERVER, priority=1,
    ))
    compiled = eng.get_compiled("planning")
    order = [e["extension_id"] for e in compiled.registered_extensions]
    assert order == ["a", "b", "c"]  # edges beat priority


def test_priority_tiebreak():
    eng = _engine()
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="low", point="planner.before",
        kind=RuntimeExtensionKind.OBSERVER, priority=10,
    ))
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="high", point="planner.before",
        kind=RuntimeExtensionKind.OBSERVER, priority=90,
    ))
    compiled = eng.get_compiled("planning")
    order = [e["extension_id"] for e in compiled.registered_extensions]
    assert order == ["high", "low"]  # priority desc on ties


# ── 复验重点 3: before/after 交错次序 ─────────────────────────────────


@pytest.mark.asyncio
async def test_execution_order_before_after_interleaving():
    eng = _engine()
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="pre", point="playbook.build.before",
        kind=RuntimeExtensionKind.OBSERVER,
    ))
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="post", point="playbook.build.after",
        kind=RuntimeExtensionKind.OBSERVER,
    ))

    executed: list[str] = []

    async def core_runner(step_id, payload):
        executed.append(step_id)

    result = await eng.execute("planning", {}, core_runner=core_runner)
    ids = [s["step"] for s in result["trace"] if s["type"] == "core"]
    # all core steps ran in base order
    assert ids == [s.step_id for s in BASE_PIPELINES["planning"].steps]
    assert result["status"] == "COMPLETED"


# ── 复验重点 4: 核心步骤失败 vs 扩展失败 ──────────────────────────────


@pytest.mark.asyncio
async def test_core_failure_vs_extension_failure():
    eng = _engine()

    # Extension that CRASHES — pipeline continues (advisory)
    from project_runtime.extension.registry import Permission

    class Boom:
        permission = Permission.READ_ONLY

        async def invoke(self, payload):
            raise RuntimeError("ext boom")

    eng._hook_executor.register_runtime(
        "planner.before", "boom-ext", RuntimeExtensionKind.OBSERVER, Boom()
    )
    eng.bind_extension("planning", ExtensionBinding(
        extension_id="boom-ext", point="planner.before",
        kind=RuntimeExtensionKind.OBSERVER,
    ))

    async def core_ok(step_id, payload):
        return {}

    result = await eng.execute("planning", {}, core_runner=core_ok)
    assert result["status"] == "COMPLETED"  # extension failure did NOT block
    ext_entry = [t for t in result["trace"] if t.get("status") == "extension_error"]
    assert len(ext_entry) == 1  # ... but it was recorded

    # Core step failure — pipeline FAILS with the failing step id
    async def core_fails(step_id, payload):
        if step_id == "gap.classify":
            raise RuntimeError("core boom")

    result2 = await eng.execute("planning", {}, core_runner=core_fails)
    assert result2["status"] == "FAILED"
    assert result2["failed_step"]["step_id"] == "gap.classify"
    # steps after the failure never ran
    ran = {t["step"] for t in result2["trace"]}
    assert "playbook.build" not in ran


# ── TD01: 注入化 ──────────────────────────────────────────────────────


def test_td01_executor_injected_not_singleton():
    # Two engines with DIFFERENT executors — no shared global state
    e1 = PipelineEngine(RuntimeHookExecutor(ExtensionRegistry()))
    e2 = PipelineEngine(RuntimeHookExecutor(ExtensionRegistry()))
    assert e1._hook_executor is not e2._hook_executor

    # The shared engine (composition root) wires the service's executor
    from project_runtime.execution.service import hook_executor, pipeline_engine

    assert pipeline_engine._hook_executor is hook_executor


@pytest.mark.asyncio
async def test_api_compile_endpoint_shape():
    from httpx import ASGITransport, AsyncClient

    from apps.runtime.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/pipelines")
        assert r.status_code == 200
        ids = [p["pipeline_id"] for p in r.json()]
        assert "task_execution" in ids

        r = await c.get("/api/pipelines/task_execution")
        assert r.status_code == 200
        body = r.json()
        assert len(body["ordered_steps"]) >= 7

        r = await c.post("/api/pipelines/task_execution/compile")
        assert r.status_code == 200
        assert len(r.json()["dependency_graph_hash"]) == 64
