"""D12 Artifact 回传 — tests for file writing, SHA-256, and version history."""

import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.artifact.manager import ArtifactManager
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import ArtifactType
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
    p = Project(name="d12-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# File writing
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_artifact_writes_file(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    artifact = await mgr.create_artifact(
        project_id=project.id,
        task_id=1,
        logical_id="output-1",
        name="result.txt",
        content="hello world",
    )

    # File exists on disk
    assert os.path.exists(artifact.path)
    assert Path(artifact.path).read_text() == "hello world"


@pytest.mark.asyncio
async def test_artifact_linked_to_task_and_project(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    artifact = await mgr.create_artifact(
        project_id=project.id,
        task_id=42,
        logical_id="report",
        name="report.md",
        content="# Report",
    )
    assert artifact.project_id == project.id
    assert artifact.task_id == 42


# ════════════════════════════════════════════════════════════════════════════
# SHA-256 integrity
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_artifact_sha256_is_correct(dbsession, project):
    import hashlib

    content = b"test content"
    expected_hash = hashlib.sha256(content).hexdigest()

    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    artifact = await mgr.create_artifact(
        project_id=project.id,
        task_id=1,
        logical_id="hash-test",
        name="data.bin",
        content=content,
    )
    assert artifact.sha256 == expected_hash
    assert len(artifact.sha256) == 64


# ════════════════════════════════════════════════════════════════════════════
# Version history (no overwrite)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_version_increments_on_same_logical_id(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")

    a1 = await mgr.create_artifact(
        project_id=project.id, task_id=1, logical_id="doc", name="v1.txt", content="v1"
    )
    a2 = await mgr.create_artifact(
        project_id=project.id, task_id=1, logical_id="doc", name="v2.txt", content="v2"
    )

    assert a1.version == 1
    assert a2.version == 2
    # Both files exist, neither overwritten
    assert os.path.exists(a1.path)
    assert os.path.exists(a2.path)


# ════════════════════════════════════════════════════════════════════════════
# List by task / project
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_by_task(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    await mgr.create_artifact(project.id, 10, "a", "a.txt", "a")
    await mgr.create_artifact(project.id, 10, "b", "b.txt", "b")
    await mgr.create_artifact(project.id, 99, "c", "c.txt", "c")

    task10_artifacts = await mgr.list_by_task(10)
    assert len(task10_artifacts) == 2


@pytest.mark.asyncio
async def test_list_by_project(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    await mgr.create_artifact(project.id, 1, "x", "x.txt", "x")
    await mgr.create_artifact(project.id, 2, "y", "y.txt", "y")

    all_artifacts = await mgr.list_by_project(project.id)
    assert len(all_artifacts) == 2


# ════════════════════════════════════════════════════════════════════════════
# ArtifactType support
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_artifact_type(dbsession, project):
    mgr = ArtifactManager(dbsession, workspace_root="data/test_tasks")
    a = await mgr.create_artifact(
        project.id, 1, "code", "main.py", "print(1)", artifact_type=ArtifactType.CODE
    )
    assert a.artifact_type == ArtifactType.CODE
