"""D08 Gap Classification — tests for GapClassifier."""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.gap.classifier import GapClassifier
from project_runtime.method_asset.importer import MethodAssetImporter
from project_runtime.procedure.normalizer import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import GapType
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
    p = Project(name="d08-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _get_assessment(dbsession, project, text):
    importer = MethodAssetImporter(dbsession)
    await importer.import_text(project.id, text, source_name="test.md")
    normalizer = ProcedureNormalizer(dbsession)
    model = await normalizer.normalize(project.id)
    assessor = MethodReadinessAssessor(dbsession)
    return await assessor.assess(project.id, model.id)


# ════════════════════════════════════════════════════════════════════════════
# Classification
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_classify_produces_gaps(dbsession, project):
    assessment = await _get_assessment(dbsession, project, "# 不完整\n输入: 需求")
    classifier = GapClassifier(dbsession)
    gaps = await classifier.classify(assessment.id)
    assert len(gaps) > 0
    for g in gaps:
        assert g.gap_type in GapType
        assert g.severity in ("HIGH", "MEDIUM", "LOW")
        assert g.resolution_strategy is not None


@pytest.mark.asyncio
async def test_gaps_have_correct_types(dbsession, project):
    assessment = await _get_assessment(
        dbsession, project,
        "# 流程\n输入: 需求\n活动: 编码",
    )
    classifier = GapClassifier(dbsession)
    gaps = await classifier.classify(assessment.id)
    types = [g.gap_type for g in gaps]
    # action_explicitness → EXPLICITNESS, scope_coverage (outputs missing) → PROCESS
    assert GapType.EXPLICITNESS in types


@pytest.mark.asyncio
async def test_missing_dimension_is_high_severity(dbsession, project):
    assessment = await _get_assessment(dbsession, project, "# 空\n")
    classifier = GapClassifier(dbsession)
    gaps = await classifier.classify(assessment.id)
    for g in gaps:
        if g.gap_type == GapType.PROCESS:
            assert g.severity == "HIGH"


@pytest.mark.asyncio
async def test_resolution_strategy_routing(dbsession, project):
    assessment = await _get_assessment(dbsession, project, "# 空缺")
    classifier = GapClassifier(dbsession)
    gaps = await classifier.classify(assessment.id)
    for g in gaps:
        if g.gap_type == GapType.EXPLICITNESS:
            assert g.resolution_strategy == "MODEL_PROPOSAL"
        elif g.gap_type == GapType.PROCESS:
            assert g.resolution_strategy == "PROJECT_METHOD_RESEARCH"


@pytest.mark.asyncio
async def test_list_gaps(dbsession, project):
    assessment = await _get_assessment(dbsession, project, "# 不完整\n输入: 需求")
    classifier = GapClassifier(dbsession)
    await classifier.classify(assessment.id)
    gaps = await classifier.list_gaps(assessment.id)
    assert len(gaps) > 0


# ════════════════════════════════════════════════════════════════════════════
# Private gaps would route to USER_CLARIFICATION
# ════════════════════════════════════════════════════════════════════════════


def test_private_gap_routes_to_user_clarification():
    from project_runtime.gap.classifier import _RESOLUTION_STRATEGIES

    assert _RESOLUTION_STRATEGIES[GapType.PRIVATE] == "USER_CLARIFICATION"


# ════════════════════════════════════════════════════════════════════════════
# Skill=0 does not trigger full research
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_skill_zero_does_not_trigger_bulk_research(dbsession, project):
    assessment = await _get_assessment(dbsession, project, "# 简单\n活动: 编码")
    classifier = GapClassifier(dbsession)
    gaps = await classifier.classify(assessment.id)
    # Gaps exist but each has a specific resolution strategy — no bulk "RESEARCH_ALL"
    for g in gaps:
        assert g.resolution_strategy != "FULL_PROJECT_RESEARCH"
