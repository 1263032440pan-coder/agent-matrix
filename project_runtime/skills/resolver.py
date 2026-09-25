"""SkillResolver — rule-first skill resolution (D17C / ADR-035).

Priority chain (fixed):
  1. Explicit Binding (project-scoped, version pinned)
  2. Explicit Reference / Preferred Skill
  3. Exact Metadata Match (domain / task_type)
  4. Generic Fallback (type match only)
  5. NO_SKILL_REQUIRED / SKILL_NOT_FOUND

Hard filters: status (resolvable only), dependencies, required capabilities,
conflicts. LLM tie-break is NOT available in V0.1 — fully-equivalent
candidates return AMBIGUOUS.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.enums import SkillScopeType, SkillType
from project_runtime.skills.models import SkillBinding, SkillDefinition
from project_runtime.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class ResolutionStatus(str, enum.Enum):
    RESOLVED = "RESOLVED"
    NO_SKILL_REQUIRED = "NO_SKILL_REQUIRED"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_DEPENDENCY_MISSING = "SKILL_DEPENDENCY_MISSING"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    AMBIGUOUS = "AMBIGUOUS"


class ResolutionReason(str, enum.Enum):
    EXPLICIT_BINDING = "EXPLICIT_BINDING"
    EXPLICIT_REFERENCE = "EXPLICIT_REFERENCE"
    PREFERRED_SKILL = "PREFERRED_SKILL"
    EXACT_METADATA_MATCH = "EXACT_METADATA_MATCH"
    GENERIC_FALLBACK = "GENERIC_FALLBACK"
    NO_SKILL_REQUIRED = "NO_SKILL_REQUIRED"


@dataclass
class SkillRequirement:
    skill_type: SkillType
    required: bool = False
    preferred_skill_id: str | None = None
    preferred_version: str | None = None
    domain: str | None = None
    task_type: str | None = None
    required_capabilities: list[str] = field(default_factory=list)


@dataclass
class SkillResolutionResult:
    status: ResolutionStatus
    resolved_skill: SkillDefinition | None = None
    candidates: list[SkillDefinition] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    reason: ResolutionReason | None = None
    binding: SkillBinding | None = None


class SkillResolver:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._registry = SkillRegistry(session)

    async def resolve(
        self,
        requirement: SkillRequirement,
        project_id: int | None = None,
        bindings: list[SkillBinding] | None = None,
        available_capabilities: set[str] | None = None,
    ) -> SkillResolutionResult:
        # ── 1. Explicit Binding ────────────────────────────────────────────
        binding = await self._find_binding(project_id, requirement, bindings)
        if binding is not None:
            skill = await self._registry.get(binding.skill_id, binding.skill_version)
            if skill is not None:
                hard = self._hard_filter([skill], requirement, available_capabilities)
                if hard.ok:
                    return SkillResolutionResult(
                        status=ResolutionStatus.RESOLVED,
                        resolved_skill=skill,
                        reason=ResolutionReason.EXPLICIT_BINDING,
                        binding=binding,
                    )
                return hard.to_failure(binding=binding)

        # ── 2. Explicit Reference / Preferred ──────────────────────────────
        if requirement.preferred_skill_id:
            if requirement.preferred_version:
                candidate = await self._registry.get(
                    requirement.preferred_skill_id, requirement.preferred_version
                )
                # Status is a hard filter even for explicit references (ADR-035)
                from project_runtime.skills.enums import AUTO_RESOLUTION_EXCLUDED

                if candidate is not None and candidate.status in AUTO_RESOLUTION_EXCLUDED:
                    candidate = None
            else:
                candidate = await self._latest_resolvable(requirement.preferred_skill_id)
            skill = candidate
            if skill is not None:
                hard = self._hard_filter([skill], requirement, available_capabilities)
                if hard.ok:
                    return SkillResolutionResult(
                        status=ResolutionStatus.RESOLVED,
                        resolved_skill=skill,
                        reason=ResolutionReason.PREFERRED_SKILL,
                    )
                return hard.to_failure()

        # ── 3. Exact Metadata Match ────────────────────────────────────────
        pool = await self._registry.list_resolvable(skill_type=requirement.skill_type)
        if requirement.domain:
            pool = [s for s in pool if _contains(s.domain, requirement.domain)]
        if requirement.task_type:
            pool = [s for s in pool if _contains(s.task_types, requirement.task_type)]
        meta_match = pool

        if len(meta_match) == 1:
            hard = self._hard_filter(meta_match, requirement, available_capabilities)
            if hard.ok:
                return SkillResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    resolved_skill=meta_match[0],
                    reason=ResolutionReason.EXACT_METADATA_MATCH,
                )
            return hard.to_failure()
        if len(meta_match) > 1:
            return SkillResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                candidates=meta_match,
                reason=ResolutionReason.EXACT_METADATA_MATCH,
            )

        # ── 4. Generic Fallback (type only) ────────────────────────────────
        fallback = await self._registry.list_resolvable(skill_type=requirement.skill_type)
        hard = self._hard_filter(fallback, requirement, available_capabilities)
        fallback_ok = hard.passed
        if len(fallback_ok) == 1:
            return SkillResolutionResult(
                status=ResolutionStatus.RESOLVED,
                resolved_skill=fallback_ok[0],
                reason=ResolutionReason.GENERIC_FALLBACK,
            )
        if len(fallback_ok) > 1:
            return SkillResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                candidates=fallback_ok,
                reason=ResolutionReason.GENERIC_FALLBACK,
            )

        # ── 5. Terminal ────────────────────────────────────────────────────
        if not hard.ok and hard.missing_dependencies:
            return hard.to_failure()
        if not hard.ok and hard.missing_capabilities:
            return hard.to_failure()

        if requirement.required:
            return SkillResolutionResult(status=ResolutionStatus.SKILL_NOT_FOUND)
        return SkillResolutionResult(
            status=ResolutionStatus.NO_SKILL_REQUIRED,
            reason=ResolutionReason.NO_SKILL_REQUIRED,
        )

    async def _find_binding(
        self,
        project_id: int | None,
        requirement: SkillRequirement,
        bindings: list[SkillBinding] | None,
    ) -> SkillBinding | None:
        if bindings:
            for b in bindings:
                return b
            return None
        if project_id is None:
            return None
        from sqlalchemy import select

        # Scope precedence: TASK > TASK_TYPE > STAGE > PROJECT_METHOD >
        # project-wide wildcard (empty scope_key — binds to any task type).
        keys = []
        if requirement.task_type:
            keys.append((SkillScopeType.TASK_TYPE, requirement.task_type))
        keys.append((SkillScopeType.PROJECT_METHOD, "main"))
        keys.append((SkillScopeType.TASK_TYPE, ""))  # project-wide wildcard
        for scope_type, key in keys:
            stmt = select(SkillBinding).where(
                SkillBinding.project_id == project_id,
                SkillBinding.scope_type == scope_type.value,
                SkillBinding.scope_key == key,
            )
            b = (await self._session.execute(stmt)).scalar_one_or_none()
            if b is not None:
                return b
        return None

    async def _latest_resolvable(self, skill_id: str) -> SkillDefinition | None:
        from sqlalchemy import select

        from project_runtime.skills.enums import AUTO_RESOLUTION_EXCLUDED

        stmt = (
            select(SkillDefinition)
            .where(
                SkillDefinition.skill_id == skill_id,
                SkillDefinition.status.notin_(list(AUTO_RESOLUTION_EXCLUDED)),
            )
            .order_by(SkillDefinition.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    class _HardResult:
        def __init__(self, passed, missing_deps, missing_caps):
            self.passed = passed
            self.missing_dependencies = missing_deps
            self.missing_capabilities = missing_caps

        @property
        def ok(self):
            return bool(self.passed)

        def to_failure(self, binding=None):
            if self.missing_dependencies:
                status = ResolutionStatus.SKILL_DEPENDENCY_MISSING
            else:
                status = ResolutionStatus.CAPABILITY_MISSING
            return SkillResolutionResult(
                status=status,
                missing_dependencies=self.missing_dependencies,
                missing_capabilities=self.missing_capabilities,
                binding=binding,
            )

    def _hard_filter(
        self,
        candidates: list[SkillDefinition],
        requirement: SkillRequirement,
        available_capabilities: set[str] | None,
    ) -> "SkillResolver._HardResult":
        passed: list[SkillDefinition] = []
        missing_deps: list[str] = []
        missing_caps: list[str] = []
        for skill in candidates:
            deps = json.loads(skill.dependencies) if skill.dependencies else []
            dep_missing = [d for d in deps]  # V0.1: any declared dep must exist in registry
            if dep_missing:
                missing_deps.extend(dep_missing)
                continue
            caps = (
                json.loads(skill.required_capabilities)
                if skill.required_capabilities
                else []
            )
            if available_capabilities is not None:
                cap_missing = [c for c in caps if c not in available_capabilities]
                if cap_missing:
                    missing_caps.extend(cap_missing)
                    continue
            passed.append(skill)
        return SkillResolver._HardResult(passed, missing_deps, missing_caps)


def _contains(json_list: str | None, value: str) -> bool:
    if not json_list:
        return False
    try:
        return value in json.loads(json_list)
    except (json.JSONDecodeError, TypeError):
        return False
