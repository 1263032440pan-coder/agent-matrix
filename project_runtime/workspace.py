"""项目工作区解析：用户指定目录优先，否则回落 data/tasks/{project_id}。"""

from __future__ import annotations

from pathlib import Path


def default_project_workspace(project_id: int) -> Path:
    return Path("data/tasks") / str(int(project_id))


def resolve_project_workspace(
    project_id: int,
    workspace_path: str | None,
) -> Path:
    """项目级工作区根（清单扫描 / Artifact 父根）。"""
    raw = (workspace_path or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return default_project_workspace(project_id).resolve()


def resolve_task_workspace(
    project_id: int,
    task_id: int,
    workspace_path: str | None,
) -> Path:
    """任务写禁闭根。

    - 未指定用户目录：保持历史布局 data/tasks/{pid}/{tid}
    - 已指定：{workspace}/.agent-matrix/tasks/{tid}，避免污染用户仓库根
    """
    raw = (workspace_path or "").strip()
    if raw:
        root = Path(raw).expanduser().resolve()
        return (root / ".agent-matrix" / "tasks" / str(int(task_id))).resolve()
    return (
        default_project_workspace(project_id) / str(int(task_id))
    ).resolve()


def validate_workspace_path(raw: str, *, must_exist: bool = False) -> Path:
    """创建项目时校验：必须是绝对路径；可选要求已存在。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("workspace_path 不能为空")
    p = Path(text).expanduser()
    if not p.is_absolute():
        raise ValueError("workspace_path 必须是绝对路径")
    resolved = p.resolve()
    if must_exist and not resolved.exists():
        raise ValueError(f"工作区不存在: {resolved}")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"workspace_path 不是目录: {resolved}")
    return resolved
