"""D17A Skill Registry — tests per D17.5 matrix A01/C01-C04 + WP-039 §3 Gate."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.models import Project
from project_runtime.skills import (
    SkillBindingManager,
    SkillDefinition,
    SkillRegistry,
    SkillRegistryError,
    SkillScopeType,
    SkillStatus,
    SkillType,
)


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d17a-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


def _skill(skill_id="demo", version="1.0.0", skill_type=SkillType.TASK_EXECUTION,
           status=SkillStatus.INSTALLED) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id, name=skill_id, version=version,
        skill_type=skill_type, status=status,
    )


# A01 — Empty registry is legal
@pytest.mark.asyncio
async def test_a01_empty_registry_legal(dbsession):
    reg = SkillRegistry(dbsession)
    assert await reg.list() == []
    assert await reg.list_resolvable() == []
    assert await reg.list_versions("nonexistent") == []


# C01 — three SkillTypes registerable
@pytest.mark.asyncio
async def test_c01_three_skill_types(dbsession):
    reg = SkillRegistry(dbsession)
    for i, st in enumerate(SkillType):
        await reg.register(_skill(f"s{i}", skill_type=st))
    assert len(await reg.list()) == 3
    assert len(await reg.list(skill_type=SkillType.PROJECT_METHOD)) == 1


# (skill_id, version) unique
@pytest.mark.asyncio
async def test_unique_skill_id_version(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("dup", "1.0.0"))
    with pytest.raises(SkillRegistryError, match="already registered"):
        await reg.register(_skill("dup", "1.0.0"))
    # Same id, different version OK
    await reg.register(_skill("dup", "1.1.0"))
    assert await reg.list_versions("dup") == ["1.0.0", "1.1.0"]


# Enable / Disable
@pytest.mark.asyncio
async def test_enable_disable(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("s", "1.0.0"))
    await reg.enable("s", "1.0.0")
    assert (await reg.get("s", "1.0.0")).status == SkillStatus.ENABLED
    await reg.disable("s", "1.0.0")
    assert (await reg.get("s", "1.0.0")).status == SkillStatus.DISABLED
    with pytest.raises(SkillRegistryError, match="not found"):
        await reg.enable("ghost", "9.9")


# C02/C03 — Disabled / Invalid excluded from automatic resolution
@pytest.mark.asyncio
async def test_c02_c03_disabled_invalid_excluded(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("ok", status=SkillStatus.ENABLED))
    await reg.register(_skill("off", status=SkillStatus.DISABLED))
    await reg.register(_skill("bad", status=SkillStatus.INVALID))
    await reg.register(_skill("old", status=SkillStatus.DEPRECATED))
    await reg.register(_skill("new", status=SkillStatus.INSTALLED))

    resolvable = await reg.list_resolvable()
    names = {s.skill_id for s in resolvable}
    assert names == {"ok", "new"}
    # list() without filter still shows all
    assert len(await reg.list()) == 5


# Project binding pinned — registry growth does not drift
@pytest.mark.asyncio
async def test_c04_project_binding_pinned(dbsession, project):
    reg = SkillRegistry(dbsession)
    bnd = SkillBindingManager(dbsession)

    await reg.register(_skill("method", "1.0.0", SkillType.PROJECT_METHOD))
    await bnd.bind(
        project.id, SkillScopeType.PROJECT_METHOD, "main", "method", "1.0.0"
    )

    # New version registered later
    await reg.register(_skill("method", "1.1.0", SkillType.PROJECT_METHOD))

    binding = await bnd.get_binding(project.id, SkillScopeType.PROJECT_METHOD, "main")
    assert binding.skill_version == "1.0.0"  # no drift
    assert binding.pinned == 1


# Binding: unknown skill rejected; upsert; unbind
@pytest.mark.asyncio
async def test_binding_unknown_skill_rejected(dbsession, project):
    bnd = SkillBindingManager(dbsession)
    with pytest.raises(Exception, match="Skill not found"):
        await bnd.bind(project.id, SkillScopeType.TASK, "t1", "ghost", "1.0")


@pytest.mark.asyncio
async def test_binding_upsert_and_unbind(dbsession, project):
    reg = SkillRegistry(dbsession)
    bnd = SkillBindingManager(dbsession)
    await reg.register(_skill("x", "1.0"))
    await reg.register(_skill("x", "2.0"))

    await bnd.bind(project.id, SkillScopeType.TASK_TYPE, "coding", "x", "1.0")
    b = await bnd.bind(project.id, SkillScopeType.TASK_TYPE, "coding", "x", "2.0")
    assert b.skill_version == "2.0"
    assert len(await bnd.list_bindings(project.id)) == 1

    await bnd.unbind(project.id, SkillScopeType.TASK_TYPE, "coding")
    assert await bnd.list_bindings(project.id) == []
