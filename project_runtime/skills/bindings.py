"""SkillBinding manager (D17A).

Project bindings are pinned by default: installing a newer skill version
never drifts an existing project's binding.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.enums import SkillScopeType
from project_runtime.skills.models import SkillBinding, SkillDefinition

logger = logging.getLogger(__name__)


class SkillBindingError(Exception):
    pass


class SkillBindingManager:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def bind(
        self,
        project_id: int,
        scope_type: SkillScopeType,
        scope_key: str,
        skill_id: str,
        skill_version: str,
        pinned: bool = True,
    ) -> SkillBinding:
        # Verify skill exists
        stmt = select(SkillDefinition).where(
            SkillDefinition.skill_id == skill_id,
            SkillDefinition.version == skill_version,
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is None:
            raise SkillBindingError(f"Skill not found: {skill_id}@{skill_version}")

        # Upsert by unique scope
        existing = await self.get_binding(project_id, scope_type, scope_key)
        if existing is not None:
            existing.skill_id = skill_id
            existing.skill_version = skill_version
            existing.pinned = pinned
            await self._session.flush()
            return existing

        binding = SkillBinding(
            project_id=project_id,
            scope_type=scope_type.value,
            scope_key=scope_key,
            skill_id=skill_id,
            skill_version=skill_version,
            pinned=pinned,
        )
        self._session.add(binding)
        await self._session.flush()
        logger.info(
            "Skill bound: project=%d %s/%s → %s@%s",
            project_id, scope_type.value, scope_key, skill_id, skill_version,
        )
        return binding

    async def get_binding(
        self, project_id: int, scope_type: SkillScopeType, scope_key: str
    ) -> SkillBinding | None:
        stmt = select(SkillBinding).where(
            SkillBinding.project_id == project_id,
            SkillBinding.scope_type == scope_type.value,
            SkillBinding.scope_key == scope_key,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_bindings(self, project_id: int) -> list[SkillBinding]:
        stmt = select(SkillBinding).where(SkillBinding.project_id == project_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def unbind(
        self, project_id: int, scope_type: SkillScopeType, scope_key: str
    ) -> None:
        binding = await self.get_binding(project_id, scope_type, scope_key)
        if binding is None:
            raise SkillBindingError(
                f"Binding not found: {project_id} {scope_type.value}/{scope_key}"
            )
        await self._session.delete(binding)
        await self._session.flush()
