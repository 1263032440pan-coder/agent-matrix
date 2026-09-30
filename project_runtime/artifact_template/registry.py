"""Artifact Template Registry + Dynamic Contract service (WP-028).

Registry 可以为空（关键原则）——模板缺席时 generate_contract() 动态产出
项目级产物规格，永不 BLOCKED。

导入安全（复验预告③，D17B 模式）: 扩展名白名单 / 路径穿越拒绝 /
大小限制——外部模板文件先过三防再入库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.artifact_template.models import (
    ArtifactTemplate,
    DynamicArtifactContract,
)

logger = logging.getLogger(__name__)

# 导入三防（D17B 模式复用）
ALLOWED_EXTENSIONS = {".md", ".markdown", ".json", ".txt", ".yaml", ".yml"}
MAX_TEMPLATE_BYTES = 512 * 1024  # 512KB

_FORBIDDEN_PATH = re.compile(r"(^|/)\.\.(/|$)|^[a-zA-Z]:[\\/]|^[\\/]")


class TemplateImportError(Exception):
    pass


class ArtifactTemplateRegistry:
    """Template 注册表——版本化资产，(template_id, version) 唯一。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── import（三防）───────────────────────────────────────────────

    def validate_import(self, name: str, content: str) -> str:
        """名称/内容三防校验，返回 content_hash。"""
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise TemplateImportError(
                f"extension {ext!r} not allowed (whitelist: "
                f"{sorted(ALLOWED_EXTENSIONS)})"
            )
        if _FORBIDDEN_PATH.search(name):
            raise TemplateImportError("path traversal pattern rejected")
        size = len(content.encode("utf-8"))
        if size > MAX_TEMPLATE_BYTES:
            raise TemplateImportError(
                f"template too large: {size} > {MAX_TEMPLATE_BYTES} bytes"
            )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def import_template(
        self, *, template_id: str, name: str, version: str, content: str,
        artifact_type: str = "DOCUMENT", structure_json: str | None = None,
    ) -> ArtifactTemplate:
        content_hash = self.validate_import(name, content)
        existing = await self.get(template_id, version)
        if existing is not None:
            raise TemplateImportError(
                f"template {template_id}@{version} already registered"
            )
        tpl = ArtifactTemplate(
            template_id=template_id, name=name, version=version,
            artifact_type=artifact_type, body=content,
            structure_json=structure_json, content_hash=content_hash,
            source="USER", status="ENABLED",
        )
        self._session.add(tpl)
        await self._session.flush()
        logger.info("ArtifactTemplate imported: %s@%s", template_id, version)
        return tpl

    # ── registry 能力 ───────────────────────────────────────────────

    async def get(self, template_id: str, version: str) -> ArtifactTemplate | None:
        stmt = select(ArtifactTemplate).where(
            ArtifactTemplate.template_id == template_id,
            ArtifactTemplate.version == version,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_templates(
        self, *, enabled_only: bool = True
    ) -> list[ArtifactTemplate]:
        stmt = select(ArtifactTemplate)
        if enabled_only:
            stmt = stmt.where(ArtifactTemplate.status == "ENABLED")
        return list((await self._session.execute(stmt)).scalars().all())

    async def find_by_type(self, artifact_type: str) -> list[ArtifactTemplate]:
        stmt = select(ArtifactTemplate).where(
            ArtifactTemplate.artifact_type == artifact_type,
            ArtifactTemplate.status == "ENABLED",
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def disable(self, template_id: str, version: str) -> ArtifactTemplate:
        tpl = await self.get(template_id, version)
        if tpl is None:
            raise TemplateImportError(f"template not found: {template_id}@{version}")
        tpl.status = "DISABLED"
        await self._session.flush()
        return tpl


class DynamicContractService:
    """模板命中→引用；无模板→动态生成。永不 BLOCKED（关键原则）。"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def generate_contracts(self, project_id: int) -> list[DynamicArtifactContract]:
        """为项目生成产物规格：Playbook outputs + WP-026 known_requirements，
        每个产物名先查模板注册表——命中引用模板，miss 动态 spec。"""
        wanted = await self._wanted_artifacts(project_id)

        # dedupe: keep contracts already generated for this project
        existing = await self._existing_names(project_id)
        created: list[DynamicArtifactContract] = []

        for name, source in wanted:
            if name in existing:
                continue
            tpl = await self._match_template(name)
            if tpl is not None:
                spec = json.loads(tpl.structure_json or "{}") if tpl.structure_json else {}
                spec["template_body_hint"] = tpl.body[:200]
                contract = DynamicArtifactContract(
                    project_id=project_id, artifact_name=name,
                    artifact_type=tpl.artifact_type,
                    spec_json=json.dumps(spec, ensure_ascii=False),
                    template_ref=f"{tpl.template_id}@{tpl.version}",
                    source="TEMPLATE",
                )
            else:
                contract = DynamicArtifactContract(
                    project_id=project_id, artifact_name=name,
                    artifact_type="DOCUMENT",
                    spec_json=json.dumps(
                        {"sections": ["概述", "内容", "验收要点"],
                         "generated": True},
                        ensure_ascii=False,
                    ),
                    template_ref=None,
                    source=source,
                )
            self._session.add(contract)
            created.append(contract)
        if created:
            await self._session.flush()
            logger.info(
                "DynamicArtifactContract x%d (project=%d, templated=%d)",
                len(created), project_id,
                sum(1 for c in created if c.template_ref),
            )
        return created

    async def list_contracts(self, project_id: int) -> list[DynamicArtifactContract]:
        stmt = select(DynamicArtifactContract).where(
            DynamicArtifactContract.project_id == project_id
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ── wanted artifacts: Playbook outputs + understanding requirements ──

    async def _wanted_artifacts(self, project_id: int) -> list[tuple[str, str]]:
        wanted: list[tuple[str, str]] = []

        # 1. Playbook workstream outputs
        from project_runtime.project_state.models import ProjectPlaybook

        stmt = (
            select(ProjectPlaybook)
            .where(ProjectPlaybook.project_id == project_id)
            .order_by(ProjectPlaybook.id.desc())
            .limit(1)
        )
        pb = (await self._session.execute(stmt)).scalar_one_or_none()
        if pb is not None and pb.workstreams:
            for ws in json.loads(pb.workstreams):
                out = ws.get("outputs")
                if not out or out == "UNKNOWN":
                    continue
                for piece in str(out).replace("\n", "，").split("，"):
                    piece = piece.strip()
                    if piece and piece != "UNKNOWN":
                        wanted.append((piece, "PLAYBOOK"))

        # 2. WP-026 known_requirements（预告⑤ 衔接）
        from project_runtime.understanding.models import ProjectUnderstanding

        stmt = (
            select(ProjectUnderstanding)
            .where(ProjectUnderstanding.project_id == project_id)
            .order_by(ProjectUnderstanding.id.desc())
            .limit(1)
        )
        und = (await self._session.execute(stmt)).scalar_one_or_none()
        if und is not None and und.known_requirements_json:
            for req in json.loads(und.known_requirements_json):
                # requirement statements name artifacts they mention
                for kw in ("文档", "报告", "清单", "设计", "方案", "代码"):
                    if kw in req:
                        wanted.append((req[:80], "REQUIREMENT"))
                        break

        return wanted

    async def _existing_names(self, project_id: int) -> set[str]:
        rows = await self.list_contracts(project_id)
        return {r.artifact_name for r in rows}

    async def _match_template(
        self, artifact_name: str
    ) -> ArtifactTemplate | None:
        """V0.1 匹配: 模板 artifact_type 命中产物名关键词。"""
        registry = ArtifactTemplateRegistry(self._session)
        for t in await registry.list_templates():
            if t.artifact_type != "DOCUMENT":
                # typed templates (CODE/REPORT/...) match by name keyword
                if t.artifact_type.lower() in artifact_name.lower():
                    return t
                continue
            # DOCUMENT 模板按名称关键词弱匹配（V0.1）——先剥扩展名
            stem = t.name.rsplit(".", 1)[0] if "." in t.name else t.name
            key = stem.lower().replace("模板", "").replace("template", "").strip()
            if key and key in artifact_name.lower():
                return t
        return None
