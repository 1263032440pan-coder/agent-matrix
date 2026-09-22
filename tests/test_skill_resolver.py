"""D17C Skill Resolver — D17.5 matrix D01-D08 + version pin."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.models import Project
from project_runtime.skills import (
    ResolutionReason,
    ResolutionStatus,
    SkillBindingManager,
    SkillRegistry,
    SkillRequirement,
    SkillResolver,
    SkillScopeType,
    SkillStatus,
    SkillType,
)
from project_runtime.skills.models import SkillDefinition


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
    p = Project(name="d17c-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


def _skill(skill_id, version="1.0.0", status=SkillStatus.ENABLED, **kw) -> SkillDefinition:
    import json

    return SkillDefinition(
        skill_id=skill_id,
        name=skill_id,
        version=version,
        skill_type=kw.pop("skill_type", SkillType.TASK_EXECUTION),
        description="fixture",
        status=status,
        **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in kw.items()},
    )


# A02 — optional, no skill → NO_SKILL_REQUIRED
@pytest.mark.asyncio
async def test_d04_optional_no_candidates(dbsession):
    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=False)
    )
    assert r.status == ResolutionStatus.NO_SKILL_REQUIRED


# A03 — required, no skill → SKILL_NOT_FOUND (never FAILED)
@pytest.mark.asyncio
async def test_d05_required_no_candidates(dbsession):
    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=True)
    )
    assert r.status == ResolutionStatus.SKILL_NOT_FOUND


# D01 — explicit binding wins
@pytest.mark.asyncio
async def test_d01_explicit_binding(dbsession, project):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("bound", "1.0.0", domain=["x"]))
    await reg.register(_skill("other", "1.0.0", domain=["x"]))

    bnd = SkillBindingManager(dbsession)
    await bnd.bind(project.id, SkillScopeType.TASK_TYPE, "coding", "bound", "1.0.0")

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION, task_type="coding", domain="x"
        ),
        project_id=project.id,
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert r.resolved_skill.skill_id == "bound"
    assert r.reason == ResolutionReason.EXPLICIT_BINDING


# D02 — preferred skill used when no binding
@pytest.mark.asyncio
async def test_d02_preferred_skill(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("pref", "1.0.0"))
    await reg.register(_skill("other", "1.0.0"))

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION,
            preferred_skill_id="pref",
            preferred_version="1.0.0",
        )
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert r.resolved_skill.skill_id == "pref"
    assert r.reason == ResolutionReason.PREFERRED_SKILL


# D03 — exact metadata match unique → RESOLVED without LLM
@pytest.mark.asyncio
async def test_d03_exact_metadata_match(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("hit", domain=["gov"], task_types=["review"]))
    await reg.register(_skill("miss", domain=["finance"]))

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION, domain="gov", task_type="review"
        )
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert r.resolved_skill.skill_id == "hit"
    assert r.reason == ResolutionReason.EXACT_METADATA_MATCH


# D06 — dependency missing
@pytest.mark.asyncio
async def test_d06_dependency_missing(dbsession, project):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("dep-needy", dependencies=["base-skill"]))
    bnd = SkillBindingManager(dbsession)
    await bnd.bind(
        project.id, SkillScopeType.PROJECT_METHOD, "main", "dep-needy", "1.0.0"
    )

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(skill_type=SkillType.TASK_EXECUTION, required=True),
        project_id=project.id,
    )
    assert r.status == ResolutionStatus.SKILL_DEPENDENCY_MISSING
    assert "base-skill" in r.missing_dependencies


# D07 — capability missing
@pytest.mark.asyncio
async def test_d07_capability_missing(dbsession, project):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("cap-needy", required_capabilities=["browser"]))
    bnd = SkillBindingManager(dbsession)
    await bnd.bind(
        project.id, SkillScopeType.PROJECT_METHOD, "main", "cap-needy", "1.0.0"
    )

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION,
            required=True,
            required_capabilities=["browser"],
        ),
        project_id=project.id,
        available_capabilities=set(),  # nothing available
    )
    assert r.status == ResolutionStatus.CAPABILITY_MISSING
    assert "browser" in r.missing_capabilities


# D08 — fully equivalent candidates → AMBIGUOUS (no LLM in V0.1)
@pytest.mark.asyncio
async def test_d08_ambiguous_no_llm(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("a", domain=["gov"], task_types=["review"]))
    await reg.register(_skill("b", domain=["gov"], task_types=["review"]))

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION, domain="gov", task_type="review"
        )
    )
    assert r.status == ResolutionStatus.AMBIGUOUS
    assert len(r.candidates) == 2


# C04 — pin holds: bound v1.0 survives registry gaining v1.1
@pytest.mark.asyncio
async def test_c04_pin_no_drift(dbsession, project):
    reg = SkillRegistry(dbsession)
    bnd = SkillBindingManager(dbsession)
    await reg.register(_skill("method", "1.0.0", skill_type=SkillType.PROJECT_METHOD))
    await bnd.bind(
        project.id, SkillScopeType.PROJECT_METHOD, "main", "method", "1.0.0"
    )
    await reg.register(_skill("method", "1.1.0", skill_type=SkillType.PROJECT_METHOD))

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(skill_type=SkillType.PROJECT_METHOD, required=True),
        project_id=project.id,
    )
    assert r.status == ResolutionStatus.RESOLVED
    assert r.resolved_skill.version == "1.0.0"


# Disabled skill never resolved even when bound explicitly referenced
@pytest.mark.asyncio
async def test_disabled_not_resolved_via_preferred(dbsession):
    reg = SkillRegistry(dbsession)
    await reg.register(_skill("off", status=SkillStatus.DISABLED))

    r = await SkillResolver(dbsession).resolve(
        SkillRequirement(
            skill_type=SkillType.TASK_EXECUTION,
            preferred_skill_id="off",
            preferred_version="1.0.0",
            required=True,
        )
    )
    # preferred path skipped (disabled → _latest_resolvable misses / get() finds
    # but hard filter excludes via list_resolvable; explicit get still returns
    # the row — but status DISABLED is in AUTO_RESOLUTION_EXCLUDED so preferred
    # lookup via _latest_resolvable yields None)
    assert r.status == ResolutionStatus.SKILL_NOT_FOUND
