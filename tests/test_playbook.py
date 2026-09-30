"""D09 Project Playbook — tests for synthesis and provenance."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.research.models  # noqa: F401 — register research table
from project_runtime.gap.classifier import GapClassifier
from project_runtime.method_asset.importer import MethodAssetImporter
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.procedure.normalizer import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.models import Project
from project_runtime.readiness.assessor import MethodReadinessAssessor


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
    p = Project(name="d09-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _full_pipeline(dbsession, project, *texts):
    """Run D05-D08 pipeline and return the assessment."""
    importer = MethodAssetImporter(dbsession)
    for text in texts:
        await importer.import_text(project.id, text, source_name="test.md")
    normalizer = ProcedureNormalizer(dbsession)
    model = await normalizer.normalize(project.id)
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    classifier = GapClassifier(dbsession)
    await classifier.classify(assessment.id)
    return assessment


# ════════════════════════════════════════════════════════════════════════════
# Basic synthesis
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_build_playbook(dbsession, project):
    await _full_pipeline(
        dbsession, project,
        "# 需求分析\n输入: 用户需求\n活动: 编写 PRD\n输出: PRD 文档\n角色: 产品经理",
    )
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    assert playbook.id is not None
    assert playbook.project_id == project.id

    # Workstreams should exist
    workstreams = json.loads(playbook.workstreams or "[]")
    assert len(workstreams) >= 1


@pytest.mark.asyncio
async def test_playbook_has_asset_refs(dbsession, project):
    await _full_pipeline(dbsession, project, "# 流程\n输入: A\n活动: B")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    asset_refs = json.loads(playbook.method_asset_refs or "[]")
    assert len(asset_refs) >= 1
    assert "id" in asset_refs[0]
    assert "type" in asset_refs[0]


# ════════════════════════════════════════════════════════════════════════════
# Provenance
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_workstream_has_source_type(dbsession, project):
    await _full_pipeline(dbsession, project, "# 流程A\n输入: 需求\n活动: 开发\n角色: 工程师")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    workstreams = json.loads(playbook.workstreams or "[]")
    for ws in workstreams:
        assert "source" in ws
        assert "type" in ws["source"]
        assert ws["source"]["type"] in ("USER_METHOD", "SKILL", "RESEARCH", "MODEL_INFERENCE")


@pytest.mark.asyncio
async def test_imported_asset_is_user_method(dbsession, project):
    """Q3: Imported assets must be classified as USER_METHOD, not SKILL."""
    await _full_pipeline(dbsession, project, "# 用户流程\n输入: 需求\n活动: 开发\n角色: 工程师")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)
    workstreams = json.loads(playbook.workstreams or "[]")
    for ws in workstreams:
        assert ws["source"]["type"] == "USER_METHOD"


@pytest.mark.asyncio
async def test_provenance_map_populated(dbsession, project):
    await _full_pipeline(dbsession, project, "# 流程\n输入: A\n活动: B")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    provenance = json.loads(playbook.provenance_map or "{}")
    assert "assets" in provenance
    assert len(provenance["assets"]) >= 1


# ════════════════════════════════════════════════════════════════════════════
# Authority levels
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_authority_in_source(dbsession, project):
    await _full_pipeline(dbsession, project, "# 流程\n输入: A\n活动: B")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    workstreams = json.loads(playbook.workstreams or "[]")
    for ws in workstreams:
        authority = ws["source"].get("authority", "")
        assert authority in ("MANDATORY", "RECOMMENDED", "OPTIONAL", "PROPOSED")


# ════════════════════════════════════════════════════════════════════════════
# Gap references
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gap_refs_in_playbook(dbsession, project):
    await _full_pipeline(dbsession, project, "# 不完整\n输入: 需求")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    gap_refs = json.loads(playbook.gap_refs or "[]")
    assert len(gap_refs) > 0
    assert "gap_type" in gap_refs[0]


@pytest.mark.asyncio
async def test_unresolved_gaps_preserved(dbsession, project):
    await _full_pipeline(dbsession, project, "# 空缺\n")
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    unresolved = json.loads(playbook.unresolved_gaps or "[]")
    assert len(unresolved) > 0


# ════════════════════════════════════════════════════════════════════════════
# Readiness snapshot
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_readiness_snapshot_in_playbook(dbsession, project):
    await _full_pipeline(
        dbsession, project,
        "# 完整\n输入: A\n活动: B\n输出: C\n角色: D\n"
        "依赖: E\n验收: F\n标准: G\n证据: H\n约束: I\n返工: J"
    )
    builder = PlaybookBuilder(dbsession)
    playbook = await builder.build(project.id)

    snapshot = json.loads(playbook.method_readiness_snapshot or "{}")
    assert "overall_status" in snapshot
