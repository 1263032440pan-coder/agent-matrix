"""D16 Delivery Contract — versions, required items, artifact mapping, missing items."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.artifact.manager import ArtifactManager
from project_runtime.delivery_contract.manager import DeliveryContractManager
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    DeliveryContractStatus,
    DeliveryItemStatus,
)
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
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="d16-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# Contract creation + versioning
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_contract_with_required_items(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    contract = await mgr.create_contract(project.id, ["report.doc", "code.zip"])

    assert contract.version == 1
    assert contract.status == DeliveryContractStatus.DRAFT
    items = await mgr.list_items(contract.id)
    assert len(items) == 2
    assert all(i.status == DeliveryItemStatus.PENDING for i in items)


@pytest.mark.asyncio
async def test_contract_version_increments(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    c1 = await mgr.create_contract(project.id, ["a"])
    c2 = await mgr.create_contract(project.id, ["a", "b"])

    assert c1.version == 1
    assert c2.version == 2
    assert c1.id != c2.id  # history preserved


@pytest.mark.asyncio
async def test_activate_contract(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    contract = await mgr.create_contract(project.id, ["x"])
    await mgr.activate(contract.id)
    await dbsession.refresh(contract)
    assert contract.status == DeliveryContractStatus.ACTIVE


# ════════════════════════════════════════════════════════════════════════════
# Artifact mapping
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_map_artifact_satisfies_item(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    contract = await mgr.create_contract(project.id, ["report.doc"])
    item = (await mgr.list_items(contract.id))[0]

    artifact_mgr = ArtifactManager(dbsession, workspace_root="data/test_delivery")
    artifact = await artifact_mgr.create_artifact(
        project_id=project.id, task_id=1, logical_id="rpt", name="report.doc", content="x"
    )

    await mgr.map_artifact(item.id, artifact.id)
    await dbsession.refresh(item)
    assert item.status == DeliveryItemStatus.SATISFIED
    assert item.artifact_ref == str(artifact.id)


@pytest.mark.asyncio
async def test_map_artifact_rejects_unknown_ids(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    contract = await mgr.create_contract(project.id, ["x"])
    item = (await mgr.list_items(contract.id))[0]

    with pytest.raises(ValueError, match="Artifact not found"):
        await mgr.map_artifact(item.id, 99999)
    with pytest.raises(ValueError, match="DeliveryItem not found"):
        await mgr.map_artifact(99999, 1)


# ════════════════════════════════════════════════════════════════════════════
# Missing items
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_missing_items_and_satisfaction(dbsession, project):
    mgr = DeliveryContractManager(dbsession)
    contract = await mgr.create_contract(project.id, ["a", "b"])
    items = await mgr.list_items(contract.id)

    assert len(await mgr.missing_items(contract.id)) == 2
    assert not await mgr.is_satisfied(contract.id)

    artifact_mgr = ArtifactManager(dbsession, workspace_root="data/test_delivery")
    for item in items:
        art = await artifact_mgr.create_artifact(
            project_id=project.id, task_id=1,
            logical_id=item.requirement_ref, name=item.requirement_ref, content="x",
        )
        await mgr.map_artifact(item.id, art.id)

    assert await mgr.missing_items(contract.id) == []
    assert await mgr.is_satisfied(contract.id)
