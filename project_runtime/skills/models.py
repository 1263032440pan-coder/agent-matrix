"""Skill Runtime entities (V1.12 / D17A).

SkillDefinition + SkillBinding. Skill content is an EXTERNAL asset —
Runtime stores metadata only (SKILL.md frontmatter + package reference).
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base
from project_runtime.skills.enums import SkillSource, SkillStatus, SkillType


class SkillDefinition(Base):
    """Registered skill metadata. (skill_id, version) globally unique."""

    __tablename__ = "skill_definition"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_type: Mapped[SkillType] = mapped_column(Enum(SkillType), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata lists stored as JSON text (V0.1)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    stages: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_inputs: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_outputs: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_capabilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    compatible_worker_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflicts: Mapped[str | None] = mapped_column(Text, nullable=True)

    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False, default="SKILL.md")
    package_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Runtime-assigned, never trusted from the package itself
    source: Mapped[SkillSource] = mapped_column(
        Enum(SkillSource), nullable=False, default=SkillSource.USER
    )
    status: Mapped[SkillStatus] = mapped_column(
        Enum(SkillStatus), nullable=False, default=SkillStatus.INSTALLED
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SkillBinding(Base):
    """Project-scoped skill binding with version pin."""

    __tablename__ = "skill_binding"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scope_type", "scope_key", name="uq_binding_scope"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # PROJECT_METHOD / STAGE / TASK_TYPE / TASK
    scope_key: Mapped[str] = mapped_column(String(256), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(256), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pinned: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
