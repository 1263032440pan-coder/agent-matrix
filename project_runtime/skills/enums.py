"""Skill Runtime enums (V1.12 / D17A)."""

from __future__ import annotations

import enum


class SkillType(str, enum.Enum):
    PROJECT_METHOD = "PROJECT_METHOD"
    TASK_EXECUTION = "TASK_EXECUTION"
    CAPABILITY = "CAPABILITY"


class SkillSource(str, enum.Enum):
    """Assigned by Runtime at import time — never trusted from the package."""

    USER = "USER"
    PROJECT = "PROJECT"
    PLUGIN = "PLUGIN"


class SkillStatus(str, enum.Enum):
    DISCOVERED = "DISCOVERED"
    VALIDATING = "VALIDATING"
    INSTALLED = "INSTALLED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    INVALID = "INVALID"
    DEPRECATED = "DEPRECATED"


class SkillScopeType(str, enum.Enum):
    PROJECT_METHOD = "PROJECT_METHOD"
    STAGE = "STAGE"
    TASK_TYPE = "TASK_TYPE"
    TASK = "TASK"


# Statuses excluded from automatic resolution (D17A Gate / 03 约束 4)
AUTO_RESOLUTION_EXCLUDED = {
    SkillStatus.INVALID,
    SkillStatus.DISABLED,
    SkillStatus.DEPRECATED,
}
