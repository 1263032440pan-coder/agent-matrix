"""Skill package API (D17B/D17C 管线的 HTTP 入口，用户可导入自己的方法流程).

Endpoints:
  POST /api/skills/import          上传 ZIP Skill 包（校验→注册）
  GET  /api/skills                 已注册 Skill 列表
  POST /api/skills/{id}/enable     启用
  POST /api/skills/{id}/disable    禁用
  POST /api/projects/{id}/skills/{skill_id}/bind   绑定到项目（scope=TASK_TYPE）
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.skills.importer import SkillImportError, SkillPackageImporter
from project_runtime.skills.registry import SkillRegistry, SkillRegistryError

router = APIRouter(prefix="/api/skills", tags=["skills"])


async def get_session():
    async with AsyncSession(engine) as session:
        yield session


def _serialize(sk) -> dict:
    return {
        "skill_id": sk.skill_id,
        "name": sk.name,
        "version": sk.version,
        "skill_type": sk.skill_type.value if sk.skill_type else None,
        "description": sk.description,
        "status": sk.status.value if sk.status else None,
        "content_hash": sk.content_hash[:12],
        "package_ref": sk.package_ref,
        "created_at": sk.created_at.isoformat() if sk.created_at else None,
    }


@router.post("/import", status_code=201)
async def import_skill_package(file: UploadFile):
    """导入 ZIP Skill 包：安全校验（禁可执行/防穿越/大小限制）→ 注册进 Registry。

    包结构（声明式）：SKILL.md（frontmatter: skill_id/name/version/skill_type/
    description）+ 可选 references/*.md。校验失败即拒绝，不入库。
    """
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip skill packages are accepted")

    content = await file.read()
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip", prefix="skill_pkg_"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        async with AsyncSession(engine) as session:
            importer = SkillPackageImporter(session)
            try:
                sk = await importer.import_zip(tmp_path)
                result = _serialize(sk)
                await session.commit()
                return result
            except SkillImportError as e:
                await session.rollback()
                raise HTTPException(422, f"Skill package rejected: {e}") from e
            except SkillRegistryError as e:
                await session.rollback()
                raise HTTPException(409, str(e)) from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.get("")
async def list_skills(
    session: AsyncSession = Depends(get_session),
):
    """已注册 Skill 列表（全部状态）。"""
    registry = SkillRegistry(session)
    skills = await registry.list()
    return [_serialize(sk) for sk in skills]


@router.post("/{skill_id}/{version}/enable", status_code=200)
async def enable_skill(skill_id: str, version: str):
    async with AsyncSession(engine) as session:
        registry = SkillRegistry(session)
        try:
            sk = await registry.enable(skill_id, version)
            result = _serialize(sk)
            await session.commit()
            return result
        except SkillRegistryError as e:
            await session.rollback()
            raise HTTPException(404, str(e)) from e


@router.post("/{skill_id}/{version}/disable", status_code=200)
async def disable_skill(skill_id: str, version: str):
    async with AsyncSession(engine) as session:
        registry = SkillRegistry(session)
        try:
            sk = await registry.disable(skill_id, version)
            result = _serialize(sk)
            await session.commit()
            return result
        except SkillRegistryError as e:
            await session.rollback()
            raise HTTPException(404, str(e)) from e


# ── Project binding（D17C：把 Skill 绑到项目，执行时 Resolver 优先采用）──────


@router.get("/bindings/project/{project_id}")
async def list_project_bindings(project_id: int):
    """项目当前全部 Skill 绑定。"""
    from project_runtime.skills.models import SkillBinding

    async with AsyncSession(engine) as session:
        stmt = select(SkillBinding).where(SkillBinding.project_id == project_id)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "project_id": b.project_id,
                "scope_type": b.scope_type,
                "scope_key": b.scope_key,
                "skill_id": b.skill_id,
                "skill_version": b.skill_version,
                "pinned": b.pinned,
            }
            for b in rows
        ]


@router.post("/bindings/project/{project_id}", status_code=200)
async def bind_skill_to_project(
    project_id: int,
    body: dict,
):
    """绑定 Skill 到项目（scope 默认 TASK_TYPE，如 scope_key='需求'）。

    body: {"skill_id", "skill_version", "scope_key"?, "scope_type"?="TASK_TYPE"}
    """
    from project_runtime.skills.bindings import SkillBindingError, SkillBindingManager
    from project_runtime.skills.enums import SkillScopeType

    skill_id = body.get("skill_id")
    skill_version = body.get("skill_version")
    if not skill_id or not skill_version:
        raise HTTPException(400, "skill_id and skill_version required")
    scope_type = SkillScopeType(body.get("scope_type", "TASK_TYPE"))
    scope_key = body.get("scope_key", "")

    async with AsyncSession(engine) as session:
        mgr = SkillBindingManager(session)
        try:
            b = await mgr.bind(
                project_id, scope_type, scope_key, skill_id, skill_version
            )
            # Snapshot BEFORE commit — ORM objects expire on commit
            result = {
                "project_id": b.project_id,
                "scope_type": b.scope_type,
                "scope_key": b.scope_key,
                "skill_id": b.skill_id,
                "skill_version": b.skill_version,
                "pinned": b.pinned,
            }
            await session.commit()
            return result
        except SkillBindingError as e:
            await session.rollback()
            raise HTTPException(404, str(e)) from e
