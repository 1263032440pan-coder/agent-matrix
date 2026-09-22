"""ArtifactManager — write Worker output to disk and register as Artifact records.

Links artifacts to Tasks with SHA-256 integrity, version increment (no overwrite).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import ArtifactType
from project_runtime.project_state.models import Artifact

logger = logging.getLogger(__name__)


class ArtifactManager:
    """Manages Artifact persistence: file writes + database records."""

    def __init__(self, session: AsyncSession, workspace_root: str = "data/tasks"):
        self._session = session
        self._workspace_root = Path(workspace_root)

    async def create_artifact(
        self,
        project_id: int,
        task_id: int,
        logical_id: str,
        name: str,
        content: str | bytes,
        artifact_type: ArtifactType = ArtifactType.FILE,
    ) -> Artifact:
        """Write content to disk and register as an Artifact."""
        task_dir = self._workspace_root / str(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)

        # Determine version — increment from last known
        latest_version = await self._get_latest_version(logical_id)
        version = latest_version + 1

        # Write file with version suffix to prevent overwrite
        stem, ext = os.path.splitext(name)
        versioned_name = f"{stem}.v{version}{ext}"
        file_path = task_dir / versioned_name
        if isinstance(content, str):
            content = content.encode("utf-8")
        file_path.write_bytes(content)

        # Compute SHA-256
        sha256 = hashlib.sha256(content).hexdigest()

        artifact = Artifact(
            logical_artifact_id=logical_id,
            project_id=project_id,
            task_id=task_id,
            artifact_type=artifact_type,
            name=name,
            version=version,
            path=str(file_path.resolve()),
            sha256=sha256,
        )
        self._session.add(artifact)
        await self._session.flush()
        logger.info("Artifact created: %s v%d sha256=%s", logical_id, version, sha256[:12])
        return artifact

    async def list_by_task(self, task_id: int) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.task_id == task_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(self, project_id: int) -> list[Artifact]:
        stmt = select(Artifact).where(Artifact.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_latest_version(self, logical_id: str) -> int:
        stmt = (
            select(Artifact.version)
            .where(Artifact.logical_artifact_id == logical_id)
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return row if row is not None else 0
