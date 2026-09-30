"""D06 Procedure Normalization — tests for extraction, provenance, and UNKNOWN markers."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.method_asset.importer import MethodAssetImporter
from project_runtime.procedure.normalizer import KeywordExtractor, ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import ReadinessStatus
from project_runtime.project_state.models import Project


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
def normalizer(dbsession: AsyncSession) -> ProcedureNormalizer:
    # Explicit keyword extractor — tests must not hit real LLM config
    return ProcedureNormalizer(dbsession, extractor=KeywordExtractor())


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d06-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _import_asset(dbsession, project, content, source_name="test.md"):
    importer = MethodAssetImporter(dbsession)
    return await importer.import_text(project.id, content, source_name=source_name)


# ════════════════════════════════════════════════════════════════════════════
# Basic normalization
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_normalize_single_asset(normalizer, project, dbsession):
    await _import_asset(
        dbsession,
        project,
        "# 需求分析\n输入: 用户故事\n活动: 编写需求文档\n输出: PRD\n角色: 产品经理",
    )
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    assert len(nodes) >= 1
    node = nodes[0]
    assert "需求分析" in node.name


@pytest.mark.asyncio
async def test_normalize_multiple_assets(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# 需求分析\n输入: 用户需求", source_name="req.md")
    await _import_asset(dbsession, project, "# 方案设计\n活动: 架构设计", source_name="design.md")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    assert len(nodes) >= 2


# ════════════════════════════════════════════════════════════════════════════
# Provenance
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_node_has_source_ref(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# 测试\n活动: 写测试用例", source_name="test_plan.md")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    for node in nodes:
        assert node.source_ref is not None
        assert "test_plan.md" in node.source_ref


@pytest.mark.asyncio
async def test_node_provenance_includes_asset_id(normalizer, project, dbsession):
    asset = await _import_asset(dbsession, project, "# 流程\n输入: A\n输出: B")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    for node in nodes:
        prov = json.loads(node.provenance or "{}")
        assert prov.get("asset_id") == asset.id


@pytest.mark.asyncio
async def test_source_map_populated(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# Step1\n活动: 做任务")
    model = await normalizer.normalize(project.id)
    source_map = json.loads(model.source_map or "[]")
    assert len(source_map) >= 1
    assert "asset_id" in source_map[0]


# ════════════════════════════════════════════════════════════════════════════
# UNKNOWN markers
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unspecified_fields_marked_unknown(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# 简单任务\n活动: 做一件事")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    node = nodes[0]
    # Fields with no keyword match should be UNKNOWN
    assert node.acceptance_criteria == "UNKNOWN"
    assert node.gate == "UNKNOWN"


# ════════════════════════════════════════════════════════════════════════════
# Readiness status
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_content_node_is_missing(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# 空节点\n", source_name="empty.md")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    for node in nodes:
        if node.name == "空节点":
            assert node.readiness_status == ReadinessStatus.MISSING


@pytest.mark.asyncio
async def test_partial_content_node_is_partial(normalizer, project, dbsession):
    await _import_asset(dbsession, project, "# 部分完整\n输入: 需求\n活动: 开发\n角色: 工程师")
    model = await normalizer.normalize(project.id)
    nodes = await normalizer.get_nodes(model.id)
    for node in nodes:
        assert node.readiness_status in (ReadinessStatus.PARTIAL, ReadinessStatus.MISSING)


# ════════════════════════════════════════════════════════════════════════════
# KeywordExtractor unit
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_extractor_markdown_sections():
    extractor = KeywordExtractor()
    text = "# 需求\n输入: 文档\n\n# 设计\n活动: 画图"
    nodes = await extractor.extract(text, "test.md")
    assert len(nodes) == 2
    assert nodes[0]["name"] == "需求"
    assert nodes[1]["name"] == "设计"


@pytest.mark.asyncio
async def test_extractor_no_markdown():
    extractor = KeywordExtractor()
    text = "做需求分析，输入是文档，活动是编码"
    nodes = await extractor.extract(text, "plain.txt")
    assert len(nodes) == 1


@pytest.mark.asyncio
async def test_extractor_unknown_is_default():
    extractor = KeywordExtractor()
    nodes = await extractor.extract("# 标题\n没有结构化内容", "x.md")
    assert nodes[0]["acceptance_criteria"] == "UNKNOWN"


# ── All 4 readiness states ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_readiness_missing():
    extractor = KeywordExtractor()
    nodes = await extractor.extract("# 空流程\n", "empty.md")
    assert nodes[0]["readiness_status"] == "MISSING"


@pytest.mark.asyncio
async def test_readiness_partial():
    extractor = KeywordExtractor()
    nodes = await extractor.extract("# 部分流程\n输入: 需求\n活动: 编码", "partial.md")
    assert nodes[0]["readiness_status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_readiness_ready():
    extractor = KeywordExtractor()
    text = (
        "# 完整流程\n"
        "输入: 需求文档\n"
        "活动: 编写代码\n"
        "输出: 可执行程序\n"
        "角色: 工程师\n"
        "依赖: 接口文档\n"
        "放行: 质检通过\n"
        "验收: 通过测试 覆盖率80%\n"
        "证据: 测试报告\n"
        "约束: 内存<512MB\n"
        "返工: 修复后重测\n"
    )
    nodes = await extractor.extract(text, "full.md")
    assert nodes[0]["readiness_status"] == "READY"


@pytest.mark.asyncio
async def test_readiness_ambiguous():
    extractor = KeywordExtractor()
    nodes = await extractor.extract("# 未定流程\n输入: 待定\n活动: TBD\n输出: 暂不明确", "ambig.md")
    assert nodes[0]["readiness_status"] == "AMBIGUOUS"


# ── D06 known limitations ─────────────────────────────────────────────────


def test_conflict_marker_enum_exists():
    """CONFLICTED enum exists but detection is not implemented yet (D07/D08)."""
    from project_runtime.project_state.enums import ReadinessStatus

    assert hasattr(ReadinessStatus, "CONFLICTED")
