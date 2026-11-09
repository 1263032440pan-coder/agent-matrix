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


def write_allow_root(task_ws: Path) -> Path:
    """写禁闭根 = 本任务工作区。"""
    return Path(task_ws).resolve()


def derive_project_workspace(task_ws: Path | None) -> Path | None:
    """从任务根推导项目读允许根（无显式 project_ws 时的兜底）。

    - `{project}/.agent-matrix/tasks/{tid}` → `{project}`
    - `data/tasks/{pid}/{tid}` → `data/tasks`（兼容跨任务只读旧测例）
    - 其它 → task_ws 本身（最窄）
    """
    if task_ws is None:
        return None
    root = Path(task_ws).resolve()
    parts = root.parts
    # .../.agent-matrix/tasks/{tid}
    if (
        len(parts) >= 3
        and parts[-2] == "tasks"
        and parts[-3] == ".agent-matrix"
    ):
        return root.parents[2]
    # .../data/tasks/{pid}/{tid}
    for i in range(len(parts) - 2):
        if parts[i] == "data" and parts[i + 1] == "tasks":
            return Path(*parts[: i + 2])
    return root


def read_allow_root(
    *,
    project_ws: Path | str | None = None,
    task_ws: Path | str | None = None,
) -> Path | None:
    """读禁闭根：显式项目根优先，否则从任务根推导。"""
    if project_ws:
        return Path(project_ws).resolve()
    if task_ws:
        return derive_project_workspace(Path(task_ws))
    return None
