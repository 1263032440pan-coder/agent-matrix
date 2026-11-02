"""Artifact content API — 让界面能直接查看每个任务步骤的产物内容."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.project_state.models import Artifact, Project
from project_runtime.workspace import resolve_project_workspace

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


async def get_session():
    async with AsyncSession(engine) as session:
        yield session


class ArtifactContent(BaseModel):
    id: int
    task_id: int | None
    name: str
    version: int
    path: str
    sha256: str | None
    content: str
    size_bytes: int


def _path_under(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@router.get("/{artifact_id}/content")
async def get_artifact_content(artifact_id: int):
    """读取产物文件内容（markdown 文本）供界面预览.

    安全：路径来自 DB 记录（ArtifactManager 落盘时生成，非用户输入），
    且强制校验文件位于项目工作区（用户指定目录或 data/tasks/{id}）内。
    """
    async with AsyncSession(engine) as session:
        art = await session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, "Artifact not found")
        proj = await session.get(Project, art.project_id)
        ws_path = proj.workspace_path if proj is not None else None
        project_id = art.project_id
        art_path = art.path
        art_id = art.id
        art_task_id = art.task_id
        art_name = art.name
        art_version = art.version
        art_sha = art.sha256

    p = Path(art_path)
    project_ws = resolve_project_workspace(project_id, ws_path)
    legacy_ws = Path("data/tasks").resolve()
    # 允许：当前项目工作区，或历史 data/tasks（未迁路径的旧产物）
    if not (
        _path_under(p, project_ws) or _path_under(p, legacy_ws)
    ):
        raise HTTPException(403, "Artifact path outside workspace")

    if not p.is_file():
        raise HTTPException(410, f"Artifact file missing on disk: {p.name}")

    # 返修单-闭环接线 P1-1: 二进制产物分流——pptx/图片等不进文本解码,
    # 返回 binary 标记 + 下载 URL（前端显示下载按钮, 不再 415 报错）
    _text_ext = (
        ".md", ".txt", ".py", ".java", ".js", ".ts", ".json",
        ".xml", ".yaml", ".yml", ".sql", ".html", ".css", ".csv",
        ".log", ".cfg", ".ini", ".toml",
    )
    if p.suffix.lower() not in _text_ext:
        return ArtifactContent(
            id=art_id,
            task_id=art_task_id,
            name=art_name,
            version=art_version,
            path=str(p),
            sha256=art_sha,
            content=(
                f"【二进制产物】{p.name}（{p.stat().st_size} 字节）——"
                f"请通过下载查看。"
            ),
            size_bytes=p.stat().st_size,
        )

    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(415, "Artifact is not UTF-8 text") from None

    return ArtifactContent(
        id=art_id,
        task_id=art_task_id,
        name=art_name,
        version=art_version,
        path=str(p),
        sha256=art_sha,
        content=text,
        size_bytes=p.stat().st_size,
    )
