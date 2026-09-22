"""SkillLoader — load a resolved skill's content for one execution (D17D).

Hard rules (WP-031 / 19 §8):
  - Verify skill_id + version + content_hash before loading.
  - Load only what the current execution needs (SKILL.md + listed refs).
  - NEVER inject the whole Registry into worker context.
  - NEVER modify Project/Task state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.models import SkillDefinition
from project_runtime.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillLoadError(Exception):
    pass


@dataclass
class SkillExecutionContext:
    """Loaded skill content for one execution. Owns no project state."""

    skill_id: str
    skill_version: str
    skill_content_hash: str
    skill_body: str
    references: dict[str, str] = field(default_factory=dict)


class SkillLoader:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._registry = SkillRegistry(session)

    async def load(
        self,
        skill_id: str,
        version: str,
        expected_hash: str | None = None,
        required_references: list[str] | None = None,
    ) -> SkillExecutionContext:
        skill = await self._registry.get(skill_id, version)
        if skill is None:
            raise SkillLoadError(f"Skill not found: {skill_id}@{version}")
        if expected_hash is not None and skill.content_hash != expected_hash:
            raise SkillLoadError(
                f"content_hash mismatch for {skill_id}@{version}: "
                f"expected {expected_hash[:12]}, got "
                f"{(skill.content_hash or '')[:12]}"
            )

        body, refs = self._read_package(skill, required_references)
        logger.info(
            "Skill loaded: %s@%s refs=%d", skill_id, version, len(refs)
        )
        return SkillExecutionContext(
            skill_id=skill.skill_id,
            skill_version=skill.version,
            skill_content_hash=skill.content_hash or "",
            skill_body=body,
            references=refs,
        )

    def _read_package(
        self, skill: SkillDefinition, required_references: list[str] | None
    ) -> tuple[str, dict[str, str]]:
        """Read only SKILL.md + explicitly required references from package_ref.

        V0.1: package_ref points to a directory (directory import). ZIP
        packages are re-read via zipfile if package_ref ends with .zip.
        """
        package_ref = skill.package_ref
        if not package_ref:
            raise SkillLoadError(f"Skill {skill.skill_id} has no package_ref")

        if package_ref.endswith(".zip"):
            return self._read_zip(package_ref, required_references)
        return self._read_dir(package_ref, required_references)

    def _read_dir(
        self, root: str, required_references: list[str] | None
    ) -> tuple[str, str]:
        root_path = Path(root)
        skill_md = root_path / "SKILL.md"
        if not skill_md.exists():
            raise SkillLoadError(f"SKILL.md missing in {root}")
        body = skill_md.read_text(encoding="utf-8")

        refs: dict[str, str] = {}
        for rel in required_references or []:
            f = root_path / rel
            _guard_relpath(rel)
            if not f.exists():
                raise SkillLoadError(f"Required reference missing: {rel}")
            refs[rel] = f.read_text(encoding="utf-8")
        return body, refs

    def _read_zip(
        self, path: str, required_references: list[str] | None
    ) -> tuple[str, dict[str, str]]:
        import zipfile

        with zipfile.ZipFile(path) as zf:
            try:
                body = zf.read("SKILL.md").decode("utf-8")
            except KeyError as e:
                raise SkillLoadError(f"SKILL.md missing in {path}") from e
            refs: dict[str, str] = {}
            for rel in required_references or []:
                _guard_relpath(rel)
                try:
                    refs[rel] = zf.read(rel).decode("utf-8")
                except KeyError as e:
                    raise SkillLoadError(f"Required reference missing: {rel}") from e
        return body, refs


def _guard_relpath(rel: str) -> None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise SkillLoadError(f"Path traversal rejected: {rel}")
