"""D07 Method Readiness — tests for 6-dimension assessment."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.method_asset.importer import MethodAssetImporter
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
    p = Project(name="d07-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _setup_with_text(dbsession, project, text):
    importer = MethodAssetImporter(dbsession)
    await importer.import_text(project.id, text, source_name="test.md")
    normalizer = ProcedureNormalizer(dbsession)
    model = await normalizer.normalize(project.id)
    return model


# ════════════════════════════════════════════════════════════════════════════
# Basic assessment
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_assess_single_node(dbsession, project):
    model = await _setup_with_text(
        dbsession, project,
        (
            "# 简单流程\n输入: 需求\n活动: 编码\n输出: 代码\n"
            "角色: 工程师\n依赖: API文档\n验收: 测试通过\n标准: 覆盖率80%\n"
            "证据: 报告\n约束: 内存<512MB\n返工: 修复后重测"
        ),
    )
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    results = json.loads(assessment.method_readiness)
    assert len(results) >= 1

    # Check 6 dimensions exist
    node = results[0]
    dims = node["dimensions"]
    for dim in ("scope_coverage", "action_explicitness", "artifact_definition",
                "acceptance_definition", "dependency_transition", "constraint_authority"):
        assert dim in dims


@pytest.mark.asyncio
async def test_assess_with_unknown_fields(dbsession, project):
    model = await _setup_with_text(
        dbsession, project,
        "# 不完整流程\n输入: 需求\n活动: 编码",
    )
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    results = json.loads(assessment.method_readiness)
    node = results[0]
    dims = node["dimensions"]

    # Scope (inputs, outputs) — inputs known, outputs unknown → PARTIAL
    assert dims["scope_coverage"]["status"] == "PARTIAL"
    # Action explicitness — activities known → COVERED
    assert dims["action_explicitness"]["status"] == "COVERED"
    # Acceptance — unknown → MISSING
    assert dims["acceptance_definition"]["status"] == "MISSING"


@pytest.mark.asyncio
async def test_assess_empty_node(dbsession, project):
    model = await _setup_with_text(
        dbsession, project,
        "# 空节点\n",
    )
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    results = json.loads(assessment.method_readiness)
    assert len(results) >= 1
    node = results[0]
    assert node["covered_dimensions"] == 0  # all MISSING


# ════════════════════════════════════════════════════════════════════════════
# Coverage summary
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_coverage_summary_populated(dbsession, project):
    model = await _setup_with_text(
        dbsession, project,
        "# 流程A\n输入: A\n活动: 做A\n# 流程B\n输入: B\n输出: B结果",
    )
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    summary = json.loads(assessment.coverage_summary or "{}")
    assert summary["total_nodes"] >= 2
    assert summary["overall_status"] in ("PARTIAL", "AMBIGUOUS", "MISSING")


# ════════════════════════════════════════════════════════════════════════════
# Multiple docs ≠ auto READY
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_many_docs_not_auto_ready(dbsession, project):
    """Having many documents does NOT automatically make readiness READY."""
    importer = MethodAssetImporter(dbsession)
    for i in range(3):
        await importer.import_text(
            project.id, f"# 文档{i}\n输入: 内容{i}", source_name=f"doc{i}.md"
        )
    normalizer = ProcedureNormalizer(dbsession)
    model = await normalizer.normalize(project.id)
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    # 3 nodes, each with only ~2 fields filled out of 10 → not READY
    summary = json.loads(assessment.coverage_summary or "{}")
    assert summary["ready"] < summary["total_nodes"]


# ════════════════════════════════════════════════════════════════════════════
# Execution capability stub
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execution_capability_is_null(dbsession, project):
    model = await _setup_with_text(dbsession, project, "# 测试\n输入: X\n活动: Y")
    assessor = MethodReadinessAssessor(dbsession)
    assessment = await assessor.assess(project.id, model.id)
    assert assessment.execution_capability_readiness is None
