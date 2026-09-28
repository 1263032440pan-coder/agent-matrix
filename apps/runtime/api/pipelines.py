"""Pipeline API (WP-035 / 04 规范 Pipeline API).

    GET  /api/pipelines
    GET  /api/pipelines/{pipeline_id}
    POST /api/pipelines/{pipeline_id}/compile
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _engine():
    from project_runtime.execution.service import pipeline_engine

    return pipeline_engine


@router.get("")
async def list_pipelines():
    return _engine().list_pipelines()


@router.get("/{pipeline_id}")
async def get_pipeline_detail(pipeline_id: str):
    try:
        compiled = _engine().get_compiled(pipeline_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, str(e)) from e
    return compiled.to_dict()


@router.post("/{pipeline_id}/compile")
async def compile_pipeline_endpoint(pipeline_id: str):
    """Recompile (enable/disable of extensions triggers this automatically;
    the endpoint forces it on demand). Returns the new hash — a CHANGED hash
    means the compiled plan actually changed (real change detection)."""
    try:
        compiled = _engine().recompile(pipeline_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Compilation failed: {e}") from e
    return {
        "pipeline_id": compiled.pipeline_id,
        "dependency_graph_hash": compiled.dependency_graph_hash,
        "ordered_steps": [s["id"] for s in compiled.ordered_steps],
        "extension_count": len(compiled.registered_extensions),
    }
