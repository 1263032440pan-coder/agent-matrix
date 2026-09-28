"""Pipeline compiler (WP-035 / 03-模型 CompiledPipeline).

Compile = base steps + injected extensions (priority / before / after)
→ ordered steps via dependency graph topological sort.

  - EXTENSION_DEPENDENCY_CYCLE blocks compilation (hard error).
  - dependency_graph_hash is a REAL change detector: recompiling with the
    same base version + same extension set yields the same hash; any change
    (base version, extension set, or order) changes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from project_runtime.extension.runtime import RuntimeExtensionKind
from project_runtime.pipeline.definition import (
    PipelineDefinition,
    PipelineError,
)


@dataclass
class ExtensionBinding:
    """One extension attached to a pipeline's extension point."""

    extension_id: str
    point: str
    kind: RuntimeExtensionKind
    priority: int = 50          # higher runs first within a point
    before: list[str] = field(default_factory=list)   # extension_ids
    after: list[str] = field(default_factory=list)


class ExtensionDependencyCycleError(PipelineError):
    """EXTENSION_DEPENDENCY_CYCLE — compilation blocked (WP-035 冲突）."""


@dataclass
class CompiledPipeline:
    pipeline_id: str
    base_version: str
    registered_extensions: list[dict]
    ordered_steps: list[dict]
    dependency_graph_hash: str
    compiled_at: str

    def to_dict(self) -> dict:
        return {
            "pipeline_id": self.pipeline_id,
            "base_version": self.base_version,
            "registered_extensions": self.registered_extensions,
            "ordered_steps": self.ordered_steps,
            "dependency_graph_hash": self.dependency_graph_hash,
            "compiled_at": self.compiled_at,
        }


def compile_pipeline(
    definition: PipelineDefinition, bindings: list[ExtensionBinding]
) -> CompiledPipeline:
    """Compile base steps + extension bindings into an ordered execution plan."""
    definition.validate()

    import datetime

    # 1. Bindings must attach to the pipeline's declared extension points
    declared = set(definition.extension_points)
    for b in bindings:
        if b.point not in declared:
            raise PipelineError(
                f"Pipeline {definition.pipeline_id}: extension {b.extension_id} "
                f"attaches to {b.point!r} which the pipeline does not declare"
            )

    # 2. Extension dependency graph (before/after) + topological sort
    order = _topo_sort_extensions(bindings)

    # 3. Interleave: point steps run when their point position occurs
    ordered_steps: list[dict] = []
    point_map: dict[str, list[ExtensionBinding]] = {}
    for b in order:
        point_map.setdefault(b.point, []).append(b)

    def _ext_step(b: ExtensionBinding) -> dict:
        return {
            "type": "extension",
            "id": b.extension_id,
            "kind": b.kind.value,
            "point": b.point,
            "priority": b.priority,
        }

    # before-points run before the pipeline's first step; after-points after
    # the last step; mid-points (worker.before_execute etc.) logically attach
    # to the step named by the point prefix.
    before_points = [p for p in declared if p.endswith(".before") or p.endswith("before_dispatch")]
    after_points = [p for p in declared if p.endswith(".after")]

    for p in before_points:
        for b in point_map.get(p, []):
            ordered_steps.append(_ext_step(b))

    for step in definition.steps:
        ordered_steps.append(
            {"type": "core", "id": step.step_id, "description": step.description}
        )
        # mid-flight points whose prefix matches this step attach right after
        for p in declared:
            if p in before_points or p in after_points:
                continue
            prefix = p.rsplit(".", 1)[0]
            if step.step_id.startswith(prefix) or prefix.startswith(step.step_id.split(".")[0]):
                for b in point_map.get(p, []):
                    ordered_steps.append(_ext_step(b))

    for p in after_points:
        for b in point_map.get(p, []):
            ordered_steps.append(_ext_step(b))

    # 4. dependency_graph_hash — real change detection over (base, exts, order)
    fingerprint_src = json.dumps(
        {
            "pipeline_id": definition.pipeline_id,
            "base_version": definition.version,
            "base_steps": [s.step_id for s in definition.steps],
            "extensions": [
                {
                    "id": b.extension_id, "point": b.point, "kind": b.kind.value,
                    "priority": b.priority, "before": sorted(b.before),
                    "after": sorted(b.after),
                }
                for b in order
            ],
            "ordered": [s["id"] for s in ordered_steps],
        },
        sort_keys=True,
    )
    dgh = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()

    return CompiledPipeline(
        pipeline_id=definition.pipeline_id,
        base_version=definition.version,
        registered_extensions=[
            {"extension_id": b.extension_id, "point": b.point, "kind": b.kind.value,
             "priority": b.priority, "before": b.before, "after": b.after}
            for b in order
        ],
        ordered_steps=ordered_steps,
        dependency_graph_hash=dgh,
        compiled_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def _topo_sort_extensions(bindings: list[ExtensionBinding]) -> list[ExtensionBinding]:
    """Topological sort by before/after edges; cycle → hard error."""
    by_id = {b.extension_id: b for b in bindings}
    if len(by_id) != len(bindings):
        dupes = [b.extension_id for b in bindings]
        seen, d = set(), []
        for x in dupes:
            if x in seen and x not in d:
                d.append(x)
            seen.add(x)
        raise PipelineError(f"Duplicate extension ids: {d}")

    # edges: a → b means a must run BEFORE b
    edges: dict[str, set[str]] = {i: set() for i in by_id}
    indeg: dict[str, int] = {i: 0 for i in by_id}

    for b in bindings:
        for later in b.before:  # b runs before `later`
            if later in by_id:
                if later not in edges[b.extension_id]:
                    edges[b.extension_id].add(later)
                    indeg[later] += 1
        for earlier in b.after:  # b runs after `earlier`
            if earlier in by_id:
                if b.extension_id not in edges[earlier]:
                    edges[earlier].add(b.extension_id)
                    indeg[b.extension_id] += 1

    # Kahn's algorithm; ties broken by priority (desc) then id (stable)
    import heapq

    heap = [(-by_id[i].priority, i) for i, d in indeg.items() if d == 0]
    heapq.heapify(heap)
    result: list[ExtensionBinding] = []
    while heap:
        _, nid = heapq.heappop(heap)
        result.append(by_id[nid])
        for nxt in sorted(edges[nid]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (-by_id[nxt].priority, nxt))

    if len(result) != len(bindings):
        remaining = sorted(set(by_id) - {b.extension_id for b in result})
        raise ExtensionDependencyCycleError(
            f"EXTENSION_DEPENDENCY_CYCLE: cycle among {remaining} — "
            f"compilation blocked"
        )
    return result
