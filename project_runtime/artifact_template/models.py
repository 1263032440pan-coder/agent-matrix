"""Artifact Template entities (WP-028 / 10-规范).

设计决策（批次 B 前置，2026-09-29）：
  ArtifactTemplate / DynamicArtifactContract 与 delivery_contract 域
  **独立 + 衔接**——规格（产物该长什么样）与验收追踪（交了没有）是两个
  关注点：Template 是产物形状的资产（对齐 SkillDefinition 模式），
  Dynamic Contract 是项目级产物规格；其产物名可作为 DeliveryItem
  .requirement_ref 的来源（衔接点登记，不强制）。

红线（关键原则）：Template Registry 可以为空——没有模板时产出
Dynamic Artifact Contract 而不是 BLOCKED。
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from project_runtime.project_state.base import Base


class ArtifactTemplate(Base):
    """可选的产物模板（版本化资产，(template_id, version) 唯一）。"""

    __tablename__ = "artifact_template"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_tpl_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False,
                                               default="DOCUMENT")
    # template body / structure spec (markdown skeleton, JSON schema, ...)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # optional machine-readable structure (chapters/fields/format rules)
    structure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="USER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ENABLED")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class DynamicArtifactContract(Base):
    """项目级产物规格——模板命中则引用模板，无模板动态生成。

    生成来源三型：
      TEMPLATE   引用了 ArtifactTemplate（template_ref 非空）
      PLAYBOOK   从 Playbook workstream outputs 生成
      REQUIREMENT 从 WP-026 known_requirements 生成
    """

    __tablename__ = "dynamic_artifact_contract"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    artifact_name: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False,
                                               default="DOCUMENT")
    # spec: required sections / format / acceptance pointers (JSON)
    spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # template hit → "template_id@version"; dynamic → None
    template_ref: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="PLAYBOOK")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
