"""Pipeline Engine (WP-035) — compile cache + execution semantics.

Execution semantics:
  - Extension steps: advisory only — an extension failure NEVER blocks the
    pipeline (WP-034 verified isolation; engine records and continues).
  - Core steps: a failure is a PIPELINE failure with the failing step id
    (clear semantics distinct from extension failures).
  - 插件/扩展调用在 State Transaction 外执行（WP-035 事务原则）— the
    engine never holds a DB transaction while invoking extension steps.

TD01 (WP-034 复验遗留): RuntimeHookExecutor is INJECTED, not a module
singleton — engine and callers receive it explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from project_runtime.extension.runtime import (
    RuntimeHookExecutor,
)
from project_runtime.pipeline.compiler import (
    CompiledPipeline,
    ExtensionBinding,
    compile_pipeline,
)
from project_runtime.pipeline.definition import (
    BASE_PIPELINES,
    get_pipeline,
)

logger = logging.getLogger(__name__)


class PipelineEngineError(Exception):
    pass


class PipelineEngine:
    """Owns compiled pipelines. Extensions register via the (injected)
    RuntimeHookExecutor; enable/disable triggers recompilation.

    Core steps execute via a runner callable: (step_id, payload) → result.
    The engine itself is transaction-free during extension invocation.
    """

    def __init__(self, hook_executor: RuntimeHookExecutor):
        # TD01: injected — no module-level singleton
        self._hook_executor = hook_executor
        self._compiled: dict[str, CompiledPipeline] = {}
        self._bindings: dict[str, list[ExtensionBinding]] = {
            pid: [] for pid in BASE_PIPELINES
        }
        # Initial compile of every base pipeline
        for pid in BASE_PIPELINES:
            self.recompile(pid)

    # ── binding + recompile ────────────────────────────────────────────

    def bind_extension(self, pipeline_id: str, binding: ExtensionBinding) -> str:
        """Attach an extension to a pipeline → triggers recompile (验收 §4).

        Transactional: if compilation fails (e.g. dependency cycle), the
        binding is NOT recorded — the pipeline stays at its last good state.
        """
        if pipeline_id not in self._bindings:
            raise PipelineEngineError(f"Unknown pipeline: {pipeline_id}")
        self._bindings[pipeline_id].append(binding)
        try:
            return self.recompile(pipeline_id).dependency_graph_hash
        except Exception:
            self._bindings[pipeline_id].remove(binding)  # rollback
            raise

    def unbind_extension(self, pipeline_id: str, extension_id: str) -> str:
        self._bindings[pipeline_id] = [
            b for b in self._bindings[pipeline_id] if b.extension_id != extension_id
        ]
        return self.recompile(pipeline_id).dependency_graph_hash

    def recompile(self, pipeline_id: str) -> CompiledPipeline:
        definition = get_pipeline(pipeline_id)
        compiled = compile_pipeline(definition, self._bindings[pipeline_id])
        old = self._compiled.get(pipeline_id)
        self._compiled[pipeline_id] = compiled
        if old is None:
            logger.info(
                "Pipeline %s compiled (hash %s…)",
                pipeline_id, compiled.dependency_graph_hash[:12],
            )
        elif old.dependency_graph_hash != compiled.dependency_graph_hash:
            logger.info(
                "Pipeline %s recompiled: hash %s… → %s… (change detected)",
                pipeline_id,
                old.dependency_graph_hash[:12],
                compiled.dependency_graph_hash[:12],
            )
        return compiled

    # ── query ───────────────────────────────────────────────────────────

    def list_pipelines(self) -> list[dict]:
        return [
            {
                "pipeline_id": pid,
                "base_version": d.version,
                "extension_points": d.extension_points,
                "steps": [s.step_id for s in d.steps],
                "extension_count": len(self._bindings[pid]),
                "compiled_hash": self._compiled[pid].dependency_graph_hash
                if pid in self._compiled else None,
            }
            for pid, d in BASE_PIPELINES.items()
        ]

    def get_compiled(self, pipeline_id: str) -> CompiledPipeline:
        if pipeline_id not in self._compiled:
            raise PipelineEngineError(f"Pipeline not compiled: {pipeline_id}")
        return self._compiled[pipeline_id]

    # ── execution ───────────────────────────────────────────────────────

    async def execute(
        self,
        pipeline_id: str,
        payload: dict[str, Any],
        core_runner: Any = None,
    ) -> dict:
        """Execute a compiled pipeline.

        core_runner: async (step_id, payload) -> dict. If absent, core steps
        are skipped (dry-run mode useful for pipeline inspection).
        Extension steps fire through the injected hook executor (advisory).
        """
        compiled = self.get_compiled(pipeline_id)
        trace: list[dict] = []
        failed_step: dict | None = None

        for step in compiled.ordered_steps:
            if step["type"] == "extension":
                # Advisory: fire the point; failures recorded, never raised.
                # No DB transaction is held here (事务原则).
                try:
                    outcome = await self._hook_executor.fire_hook(
                        step["point"], payload
                    )
                    # Individual extension failures are recorded INSIDE
                    # outcome.results (executor isolates them) — surface them.
                    bad = [
                        r for r in outcome.results
                        if r.get("result") in ("EXTENSION_ERROR", "REJECTED_PERMISSION")
                    ]
                    trace.append(
                        {"step": step["id"], "type": "extension",
                         "status": "extension_error" if bad else "ok",
                         "control": outcome.control.value,
                         **({"detail": bad[0].get("detail", bad[0]["result"])} if bad else {})}
                    )
                except Exception as e:  # noqa: BLE001 — isolation (WP-034)
                    trace.append(
                        {"step": step["id"], "type": "extension",
                         "status": "extension_error", "detail": str(e)[:120]}
                    )
            else:
                # Core step: failures are PIPELINE failures (distinct semantics)
                if core_runner is None:
                    trace.append({"step": step["id"], "type": "core", "status": "skipped"})
                    continue
                try:
                    await core_runner(step["id"], payload)
                    trace.append({"step": step["id"], "type": "core", "status": "ok"})
                except Exception as e:  # noqa: BLE001 — core failure stops pipeline
                    trace.append(
                        {"step": step["id"], "type": "core",
                         "status": "failed", "detail": str(e)[:120]}
                    )
                    failed_step = {"step_id": step["id"], "error": str(e)[:200]}
                    break

        return {
            "pipeline_id": pipeline_id,
            "dependency_graph_hash": compiled.dependency_graph_hash,
            "status": "FAILED" if failed_step else "COMPLETED",
            "failed_step": failed_step,
            "trace": trace,
        }


# Shared engine instance (composition root wires the injected executor).
# TD01: the runner's hook_executor is passed in explicitly at construction.
_shared: PipelineEngine | None = None


def get_shared_engine(hook_executor: RuntimeHookExecutor | None = None) -> PipelineEngine:
    """Composition root. The execution service constructs it with ITS
    injected executor — single wiring point, no hidden globals inside
    engine/executor themselves."""
    global _shared
    if _shared is None:
        if hook_executor is None:
            raise PipelineEngineError(
                "first construction requires an injected RuntimeHookExecutor"
            )
        _shared = PipelineEngine(hook_executor)
    return _shared
