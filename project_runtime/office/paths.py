"""统一媒体输出路径合同（相对任务工作区根）。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.worker.tools import (
    _within,
    resolve_task_path,
    resolve_workspace_path,
    tool_error,
)


def _sanitize_rel(path: str) -> str | None:
    """拒绝绝对路径与 ..；保留子目录分隔符。返回正斜杠相对路径或 None。"""
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("~"):
        return None
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        return None
    # 仅剔除 Windows 非法字符，保留 /
    cleaned = []
    for part in p.parts:
        if part in (".",):
            continue
        cleaned.append(
            "".join(ch if ch not in '<>:"|?*' else "_" for ch in part)
        )
    if not cleaned:
        return None
    return "/".join(cleaned)


def resolve_output_path(
    payload: dict,
    *,
    path: str | None = None,
    filename: str | None = None,
    default_name: str = "output.bin",
) -> tuple[Path, str] | dict:
    """解析输出绝对路径与相对展示路径。

    优先 `path`；否则用 `filename`（可含子目录）；都没有用 default_name。
    有 `__ws_root` 时强制相对该根；否则回落 data/tasks 禁闭。
    失败返回 tool_error dict。
    """
    rel = _sanitize_rel(path or "") if path else None
    if rel is None and filename:
        rel = _sanitize_rel(filename)
    if rel is None:
        rel = _sanitize_rel(default_name)
    if rel is None:
        return tool_error(
            "INVALID_PATH",
            "输出 path 非法：禁止绝对路径与 '..'",
            '示例: {"path": "deliverables/slides/方案.pptx"}',
        )

    ws_raw = payload.get("__ws_root")
    try:
        if ws_raw:
            ws_root = Path(str(ws_raw)).resolve()
            resolved = resolve_task_path(rel, ws_root)
            if not _within(resolved, ws_root):
                return tool_error(
                    "OUT_OF_SCOPE",
                    f"路径越界: {rel}",
                    "请使用相对任务工作区的路径",
                )
            display = resolved.relative_to(ws_root).as_posix()
        else:
            # 无任务根：落在 data/tasks 下相对 cwd 解析
            resolved = resolve_workspace_path(rel)
            display = rel
    except ValueError as e:
        return tool_error(
            "OUT_OF_SCOPE", str(e),
            "请使用相对任务工作区的路径",
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved, display
