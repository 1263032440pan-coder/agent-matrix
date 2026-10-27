"""Skill package API (D17B/D17C 管线的 HTTP 入口，用户可导入自己的方法流程).

Endpoints:
  POST /api/skills/import          上传 ZIP Skill 包（校验→注册）
  GET  /api/skills                 已注册 Skill 列表
  POST /api/skills/{id}/enable     启用
  POST /api/skills/{id}/disable    禁用
  POST /api/projects/{id}/skills/{skill_id}/bind   绑定到项目（scope=TASK_TYPE）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
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
        "when_to_use": sk.when_to_use,
        "domain": sk.domain,
        "task_types": sk.task_types,
        "status": sk.status.value if sk.status else None,
        "content_hash": sk.content_hash[:12],
        "package_ref": sk.package_ref,
        "created_at": sk.created_at.isoformat() if sk.created_at else None,
    }


@router.post("/import", status_code=201)
async def import_skill_package(
    file: UploadFile,
    allow_executable: bool = Form(False),
):
    """导入 ZIP Skill 包：安全校验 → 注册进 Registry。

    默认文本四防；allow_executable=true 启用 ADR-039 EXECUTION 层级
    （.py/.sh + 提额；执行型包建议先过四闸再开此开关）。
    """
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip skill packages are accepted")

    content = await file.read()
    # Persist the package under data/skills/ — the Skill Loader resolves
    # package_ref on demand; a temp file would be deleted and break loading
    # (bug seen live: package_ref pointed at an already-deleted Temp path).
    skills_dir = Path("data/skills")
    skills_dir.mkdir(parents=True, exist_ok=True)
    ts = int(__import__("time").time())
    persist_path = skills_dir / f"import_{ts}_{file.filename or 'pkg.zip'}"
    persist_path.write_bytes(content)
    tmp_path = str(persist_path)
    try:
        async with AsyncSession(engine) as session:
            importer = SkillPackageImporter(session)
            try:
                sk = await importer.import_zip(
                    tmp_path, allow_executable=allow_executable,
                )
                result = _serialize(sk)
                result["allow_executable"] = allow_executable
                await session.commit()
                return result
            except SkillImportError as e:
                await session.rollback()
                Path(tmp_path).unlink(missing_ok=True)  # rejected: drop file
                raise HTTPException(422, f"Skill package rejected: {e}") from e
            except SkillRegistryError as e:
                await session.rollback()
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001 — never a raw 500
                await session.rollback()
                Path(tmp_path).unlink(missing_ok=True)
                raise HTTPException(
                    422, f"Skill 包处理失败: {type(e).__name__}: {e}"
                ) from e
    finally:
        # Keep the persisted package (data/skills/) — it IS the load source.
        pass


@router.get("")
async def list_skills(
    q: str | None = None,
    skill_type: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """已注册 Skill 列表（全部状态）+ 元数据查询。

    q: 关键词（name/description/when_to_use/domain/task_types 模糊匹配）
    skill_type: 精确（PROJECT_METHOD/TASK_EXECUTION/...）
    status: 精确（INSTALLED/ENABLED/DISABLED/...）
    """
    from sqlalchemy import or_

    from project_runtime.skills.models import SkillDefinition

    stmt = select(SkillDefinition).order_by(SkillDefinition.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SkillDefinition.name.ilike(like),
                SkillDefinition.description.ilike(like),
                SkillDefinition.when_to_use.ilike(like),
                SkillDefinition.domain.ilike(like),
                SkillDefinition.task_types.ilike(like),
                SkillDefinition.skill_id.ilike(like),
            )
        )
    if skill_type:
        stmt = stmt.where(SkillDefinition.skill_type == skill_type)
    if status:
        stmt = stmt.where(SkillDefinition.status == status)
    skills = (await session.execute(stmt)).scalars().all()
    return [_serialize(sk) for sk in skills]


@router.delete("/{skill_id}/{version}", status_code=200)
async def delete_skill(skill_id: str, version: str):
    """删除已安装 skill: 解绑全部项目引用 + 清理包文件 + 删注册行。

    用户决策（2026-10-09）: skill 装删全由用户管理（安装=导入 ZIP，
    删除=本端点）；程序只选用，不产生/不删除 skill。
    """
    from pathlib import Path as _Path

    from sqlalchemy import delete as _delete
    from sqlalchemy import select as _select

    from project_runtime.skills.models import SkillBinding, SkillDefinition

    async with AsyncSession(engine) as session:
        sk = (
            await session.execute(
                _select(SkillDefinition).where(
                    SkillDefinition.skill_id == skill_id,
                    SkillDefinition.version == version,
                )
            )
        ).scalar_one_or_none()
        if sk is None:
            raise HTTPException(404, f"Skill not found: {skill_id}@{version}")

        package_ref = sk.package_ref
        snapshot = _serialize(sk)

        # 1) 解绑全部引用（悬挂 FK 清理）
        await session.execute(
            _delete(SkillBinding).where(SkillBinding.skill_id == skill_id)
        )
        # 2) 删注册行
        await session.delete(sk)
        await session.commit()

    # 3) 包文件 best-effort 清理（DB 是事实源；目录/zip 双形态）
    removed_files = False
    if package_ref:
        try:
            p = _Path(package_ref)
            if p.is_dir():
                import shutil

                shutil.rmtree(p, ignore_errors=True)
                removed_files = True
            elif p.is_file():
                p.unlink(missing_ok=True)
                removed_files = True
        except OSError:
            pass

    return {
        **snapshot,
        "deleted": True,
        "bindings_removed": True,
        "package_files_removed": removed_files,
    }


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
