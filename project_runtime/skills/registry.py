"""SkillRegistry — register/get/list/enable/disable/version lookup (D17A).

Hard rules:
  - Empty registry is a legal state (A01).
  - (skill_id, version) unique.
  - INVALID / DISABLED / DEPRECATED never returned by list_resolvable().
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.enums import (
    AUTO_RESOLUTION_EXCLUDED,
    SkillStatus,
    SkillType,
)
from project_runtime.skills.models import SkillDefinition

logger = logging.getLogger(__name__)


class SkillRegistryError(Exception):
    pass


class SkillRegistry:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def register(self, skill: SkillDefinition) -> SkillDefinition:
        """Register a skill. Rejects duplicate (skill_id, version)."""
        existing = await self.get(skill.skill_id, skill.version)
        if existing is not None:
            raise SkillRegistryError(
                f"Skill already registered: {skill.skill_id}@{skill.version}"
            )
        self._session.add(skill)
        await self._session.flush()
        logger.info("Skill registered: %s@%s (%s)", skill.skill_id, skill.version, skill.skill_type)
        return skill

    async def get(self, skill_id: str, version: str) -> SkillDefinition | None:
        stmt = select(SkillDefinition).where(
            SkillDefinition.skill_id == skill_id,
            SkillDefinition.version == version,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        skill_type: SkillType | None = None,
        status: SkillStatus | None = None,
    ) -> list[SkillDefinition]:
        stmt = select(SkillDefinition)
        if skill_type is not None:
            stmt = stmt.where(SkillDefinition.skill_type == skill_type)
        if status is not None:
            stmt = stmt.where(SkillDefinition.status == status)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_resolvable(
        self, skill_type: SkillType | None = None
    ) -> list[SkillDefinition]:
        """Only INSTALLED/ENABLED skills participate in automatic resolution."""
        stmt = select(SkillDefinition).where(
            SkillDefinition.status.notin_(list(AUTO_RESOLUTION_EXCLUDED))
        )
        if skill_type is not None:
            stmt = stmt.where(SkillDefinition.skill_type == skill_type)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_versions(self, skill_id: str) -> list[str]:
        stmt = (
            select(SkillDefinition.version)
            .where(SkillDefinition.skill_id == skill_id)
            .order_by(SkillDefinition.id)
        )
        return [r for (r,) in (await self._session.execute(stmt)).all()]

    async def enable(self, skill_id: str, version: str) -> SkillDefinition:
        return await self._set_status(skill_id, version, SkillStatus.ENABLED)

    async def disable(self, skill_id: str, version: str) -> SkillDefinition:
        return await self._set_status(skill_id, version, SkillStatus.DISABLED)

    async def _set_status(
        self, skill_id: str, version: str, status: SkillStatus
    ) -> SkillDefinition:
        skill = await self.get(skill_id, version)
        if skill is None:
            raise SkillRegistryError(f"Skill not found: {skill_id}@{version}")
        skill.status = status
        await self._session.flush()
        return skill
