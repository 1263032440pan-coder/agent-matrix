"""MethodAssetImporter — import raw method assets from files or pasted text."""

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import AssetType
from project_runtime.project_state.models import MethodAsset

# ── type classification heuristics (simple keyword matching for V0.1) ──────

_TYPE_KEYWORDS: dict[AssetType, list[str]] = {
    AssetType.PROCESS: ["流程", "process", "workflow", "步骤", "stage", "phase", "阶段"],
    AssetType.SOP: ["sop", "标准操作", "操作规范", "操作手册", "standard operating"],
    AssetType.STANDARD: ["标准", "standard", "规范", "specification", "规格"],
    AssetType.TEMPLATE: ["模板", "template", "模版", "form"],
    AssetType.ACCEPTANCE_RULE: ["验收", "acceptance", "检查", "checklist", "review"],
    AssetType.ROLE_DEFINITION: ["角色", "role", "职责", "岗位", "responsibility"],
    AssetType.DELIVERY_RULE: ["交付", "delivery", "deliverable", "产出", "提交"],
    AssetType.HISTORICAL_REFERENCE: ["历史", "history", "archive", "previous", "旧版"],
}


def _classify_asset_type(name: str | None, content: str) -> AssetType:
    """Heuristic classification based on filename and content keywords (V0.1)."""
    text = (name or "") + " " + content[:2000]
    text_lower = text.lower()

    scores: dict[AssetType, int] = {}
    for atype, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[atype] = score

    if scores:
        return max(scores, key=lambda k: scores[k])
    return AssetType.PROCESS  # default


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── importer ────────────────────────────────────────────────────────────────


class MethodAssetImporter:
    """Import method assets into a project."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def import_text(
        self,
        project_id: int,
        content: str,
        *,
        source_name: str = "pasted",
        asset_type: AssetType | None = None,
    ) -> MethodAsset:
        """Import from pasted/entered text."""
        chash = _content_hash(content)

        # Deduplicate by content hash
        existing = await self._find_by_hash(project_id, chash)
        if existing:
            return existing

        atype = asset_type or _classify_asset_type(source_name, content)
        asset = MethodAsset(
            project_id=project_id,
            asset_type=atype,
            source_path=source_name,
            content=content,
            content_hash=chash,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def import_file(self, project_id: int, file_path: str | Path) -> MethodAsset:
        """Import from a .md or .txt file on disk."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Asset file not found: {file_path}")

        content = path.read_text(encoding="utf-8")
        chash = _content_hash(content)

        existing = await self._find_by_hash(project_id, chash)
        if existing:
            return existing

        atype = _classify_asset_type(path.name, content)
        asset = MethodAsset(
            project_id=project_id,
            asset_type=atype,
            source_path=str(path.resolve()),
            content=content,
            content_hash=chash,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def list_assets(self, project_id: int) -> list[MethodAsset]:
        stmt = select(MethodAsset).where(MethodAsset.project_id == project_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _find_by_hash(self, project_id: int, content_hash: str) -> MethodAsset | None:
        stmt = select(MethodAsset).where(
            MethodAsset.project_id == project_id,
            MethodAsset.content_hash == content_hash,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
