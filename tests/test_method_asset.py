"""D05 Method Asset Import — tests for file import, text paste, type classification, and dedup."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.method_asset.importer import (
    MethodAssetImporter,
    _classify_asset_type,
    _content_hash,
)
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AssetType
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
def importer(dbsession: AsyncSession) -> MethodAssetImporter:
    return MethodAssetImporter(dbsession)


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d05-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# Text import
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_import_text(importer, project):
    asset = await importer.import_text(project.id, "需求分析 → 方案设计 → 开发 → 测试")
    assert asset.id is not None
    assert asset.project_id == project.id
    assert asset.content == "需求分析 → 方案设计 → 开发 → 测试"
    assert len(asset.content_hash) == 64


@pytest.mark.asyncio
async def test_import_text_with_explicit_type(importer, project):
    asset = await importer.import_text(
        project.id, "验收标准：所有测试通过", asset_type=AssetType.ACCEPTANCE_RULE
    )
    assert asset.asset_type == AssetType.ACCEPTANCE_RULE


# ════════════════════════════════════════════════════════════════════════════
# File import
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_import_file(importer, project):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("# 项目流程\n\n1. 需求\n2. 开发\n3. 验收")
        tmp_path = f.name

    try:
        asset = await importer.import_file(project.id, tmp_path)
        assert asset.id is not None
        assert "需求" in asset.content
        assert asset.source_path is not None
        assert Path(asset.source_path).name.endswith(".md")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_import_file_not_found(importer, project):
    with pytest.raises(FileNotFoundError):
        await importer.import_file(project.id, "/nonexistent/file.md")


# ════════════════════════════════════════════════════════════════════════════
# Deduplication
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_import_duplicate_text_returns_existing(importer, project):
    content = "SOP: 每日备份数据库"
    a1 = await importer.import_text(project.id, content)
    a2 = await importer.import_text(project.id, content)
    assert a1.id == a2.id  # same asset


@pytest.mark.asyncio
async def test_import_duplicate_file_returns_existing(importer, project):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("标准操作规范 v1.0")
        tmp_path = f.name

    try:
        a1 = await importer.import_file(project.id, tmp_path)
        a2 = await importer.import_file(project.id, tmp_path)
        assert a1.id == a2.id
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════════════════
# Type classification
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auto_classify_process(importer, project):
    asset = await importer.import_text(project.id, "项目阶段流程：第一步需求分析，第二步方案设计")
    assert asset.asset_type == AssetType.PROCESS


@pytest.mark.asyncio
async def test_auto_classify_sop(importer, project):
    asset = await importer.import_text(project.id, "标准操作规范 SOP：服务器部署流程")
    assert asset.asset_type == AssetType.SOP


@pytest.mark.asyncio
async def test_auto_classify_acceptance(importer, project):
    asset = await importer.import_text(project.id, "验收清单 checklist：功能测试通过、性能达标")
    assert asset.asset_type == AssetType.ACCEPTANCE_RULE


@pytest.mark.asyncio
async def test_auto_classify_role(importer, project):
    asset = await importer.import_text(project.id, "岗位职责：项目经理负责整体协调")
    assert asset.asset_type == AssetType.ROLE_DEFINITION


@pytest.mark.asyncio
async def test_classify_fallback_to_process(importer, project):
    asset = await importer.import_text(project.id, "这是一段没有明显分类特征的文本")
    assert asset.asset_type == AssetType.PROCESS


# ════════════════════════════════════════════════════════════════════════════
# Content hash
# ════════════════════════════════════════════════════════════════════════════


def test_content_hash_deterministic():
    h1 = _content_hash("hello")
    h2 = _content_hash("hello")
    assert h1 == h2
    assert len(h1) == 64


def test_content_hash_different():
    assert _content_hash("a") != _content_hash("b")


# ════════════════════════════════════════════════════════════════════════════
# List assets
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_assets(importer, project):
    await importer.import_text(project.id, "流程1")
    await importer.import_text(project.id, "流程2")
    assets = await importer.list_assets(project.id)
    assert len(assets) == 2


# ════════════════════════════════════════════════════════════════════════════
# Unit tests for classifier
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "name,content,expected",
    [
        ("流程.md", "需求分析阶段", AssetType.PROCESS),
        ("sop文档.txt", "标准操作", AssetType.SOP),
        ("技术规范.md", "系统设计标准", AssetType.STANDARD),
        ("模板.docx", "使用此模板", AssetType.TEMPLATE),
        ("验收标准.md", "功能验收检查", AssetType.ACCEPTANCE_RULE),
        ("角色定义.md", "开发工程师职责", AssetType.ROLE_DEFINITION),
        ("交付清单.md", "交付物列表", AssetType.DELIVERY_RULE),
        ("旧版方案.md", "历史项目总结", AssetType.HISTORICAL_REFERENCE),
    ],
)
def test_classify_asset_type_parametrized(name, content, expected):
    assert _classify_asset_type(name, content) == expected


# ── authority / version defaults ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_authority_defaults_to_optional(importer, project):
    asset = await importer.import_text(project.id, "内容")
    assert asset.authority.value == "OPTIONAL"


@pytest.mark.asyncio
async def test_version_defaults_to_one(importer, project):
    asset = await importer.import_text(project.id, "内容")
    assert asset.version == 1
