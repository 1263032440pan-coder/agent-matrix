"""DeliveryContractManager — manage delivery contracts, items, and artifact mapping.

V0.1 scope: contract versions, required items, artifact mapping, missing items.
Project Completion formula deferred to D18 E2E.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.enums import (
    DeliveryContractStatus,
    DeliveryItemStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    DeliveryContract,
    DeliveryItem,
)

logger = logging.getLogger(__name__)


class DeliveryContractManager:
    """Manage delivery contracts and their items."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── Contract lifecycle ─────────────────────────────────────────────────

    async def create_contract(
        self, project_id: int, required_items: list[str]
    ) -> DeliveryContract:
        """Create a new contract version with required items.

        Each call creates a new version (no overwrite of history).
        """
        version = await self._next_version(project_id)
        contract = DeliveryContract(
            project_id=project_id,
            version=version,
            status=DeliveryContractStatus.DRAFT,
            items=json.dumps(required_items),
        )
        self._session.add(contract)
        await self._session.flush()

        for ref in required_items:
            item = DeliveryItem(
                contract_id=contract.id,
                requirement_ref=ref,
                status=DeliveryItemStatus.PENDING,
            )
            self._session.add(item)
        await self._session.flush()
        logger.info(
            "DeliveryContract created: project=%d v%d items=%d",
            project_id, version, len(required_items),
        )
        return contract

    async def activate(self, contract_id: int) -> DeliveryContract:
        contract = await self._get_contract(contract_id)
        contract.status = DeliveryContractStatus.ACTIVE
        await self._session.flush()
        return contract

    # ── Artifact mapping ───────────────────────────────────────────────────

    async def map_artifact(self, item_id: int, artifact_id: int) -> DeliveryItem:
        """Map an Artifact to a DeliveryItem and mark it SATISFIED."""
        item = await self._session.get(DeliveryItem, item_id)
        if item is None:
            raise ValueError(f"DeliveryItem not found: {item_id}")
        artifact = await self._session.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError(f"Artifact not found: {artifact_id}")

        item.artifact_ref = str(artifact.id)
        item.status = DeliveryItemStatus.SATISFIED
        await self._session.flush()
        return item

    # ── Queries ────────────────────────────────────────────────────────────

    async def missing_items(self, contract_id: int) -> list[DeliveryItem]:
        """Return required items not yet SATISFIED."""
        stmt = select(DeliveryItem).where(
            DeliveryItem.contract_id == contract_id,
            DeliveryItem.status != DeliveryItemStatus.SATISFIED,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def is_satisfied(self, contract_id: int) -> bool:
        return len(await self.missing_items(contract_id)) == 0

    async def get_contract(self, contract_id: int) -> DeliveryContract:
        return await self._get_contract(contract_id)

    async def list_items(self, contract_id: int) -> list[DeliveryItem]:
        stmt = select(DeliveryItem).where(DeliveryItem.contract_id == contract_id)
        return list((await self._session.execute(stmt)).scalars().all())

    # ── internals ──────────────────────────────────────────────────────────

    async def _get_contract(self, contract_id: int) -> DeliveryContract:
        contract = await self._session.get(DeliveryContract, contract_id)
        if contract is None:
            raise ValueError(f"DeliveryContract not found: {contract_id}")
        return contract

    async def _next_version(self, project_id: int) -> int:
        stmt = (
            select(DeliveryContract.version)
            .where(DeliveryContract.project_id == project_id)
            .order_by(DeliveryContract.version.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return (row or 0) + 1
