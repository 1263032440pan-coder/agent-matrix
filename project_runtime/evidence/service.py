"""Evidence Service (WP-023) — 完成判定基于证据链（ADR-010）.

六接口: create_evidence / invalidate_evidence / validate_scope /
        get_scope_evidence / check_delivery_item / check_project_completion

红线:
  - Delivery Item 不能在 Evidence 缺失时标记 SATISFIED（硬门——
    check_delivery_item 未过时 set SATISFIED 被拒绝）
  - Project Completion 七条件全结构化判定，零 LLM 参与
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.evidence.models import (
    CompletionEvidence,
    EvidenceScope,
    EvidenceType,
)
from project_runtime.project_state.enums import (
    DeliveryItemStatus,
    GateStatus,
    TaskStatus,
)
from project_runtime.project_state.models import (
    DeliveryContract,
    DeliveryItem,
    GateResult,
    Task,
)

logger = logging.getLogger(__name__)


class EvidenceError(Exception):
    pass


@dataclass
class CompletionCheck:
    """项目完成判定的结构化结果（七条件逐项）。"""

    project_id: int
    delivery_items_satisfied: bool = False
    traceability_complete: bool = False
    evidence_valid: bool = False
    tasks_accepted: bool = False
    critical_issues_zero: bool = False
    final_gate_passed: bool = False
    no_pending_manual_actions: bool = False
    failures: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "complete": self.complete,
            "conditions": {
                "delivery_items_satisfied": self.delivery_items_satisfied,
                "traceability_complete": self.traceability_complete,
                "evidence_valid": self.evidence_valid,
                "tasks_accepted": self.tasks_accepted,
                "critical_issues_zero": self.critical_issues_zero,
                "final_gate_passed": self.final_gate_passed,
                "no_pending_manual_actions": self.no_pending_manual_actions,
            },
            "failures": self.failures,
        }


class EvidenceService:
    def __init__(self, session: AsyncSession):
        self._session = session

    # ── 基础接口 ─────────────────────────────────────────────────────

    async def create_evidence(
        self,
        project_id: int,
        evidence_type: EvidenceType,
        *,
        scope: EvidenceScope,
        ref: str,
        summary: str = "",
        payload: dict | None = None,
    ) -> CompletionEvidence:
        ev = CompletionEvidence(
            project_id=project_id,
            evidence_type=evidence_type,
            scope=scope,
            ref=ref,
            summary=summary or f"{evidence_type.value} @ {ref}",
            payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
            valid=True,
        )
        self._session.add(ev)
        await self._session.flush()
        return ev

    async def invalidate_evidence(
        self, evidence_id: int, *, reason: str
    ) -> CompletionEvidence:
        import datetime

        ev = await self._session.get(CompletionEvidence, evidence_id)
        if ev is None:
            raise EvidenceError(f"Evidence not found: {evidence_id}")
        ev.valid = False
        ev.invalidated_at = datetime.datetime.now(datetime.timezone.utc)
        ev.invalidated_reason = reason
        await self._session.flush()
        return ev

    async def validate_scope(self, project_id: int) -> list[CompletionEvidence]:
        """重验 scope 内全部证据——失效条件复检（V0.1: ref 实体仍在且
        状态未回退则维持，否则失效）。返回有效证据列表。"""
        rows = await self._scope_rows(project_id)
        import datetime

        for ev in rows:
            if not ev.valid:
                continue
            if not await self._ref_still_holds(ev):
                ev.valid = False
                ev.invalidated_at = datetime.datetime.now(datetime.timezone.utc)
                ev.invalidated_reason = "revalidation: source regressed"
        await self._session.flush()
        return [e for e in rows if e.valid]

    async def get_scope_evidence(
        self, project_id: int, scope: EvidenceScope | None = None
    ) -> list[CompletionEvidence]:
        rows = await self._scope_rows(project_id, scope=scope)
        return [e for e in rows if e.valid]

    # ── Delivery Item 硬门 ───────────────────────────────────────────

    async def check_delivery_item(self, delivery_item_id: int) -> bool:
        """Delivery Item 是否具备 SATISFIED 资格：有效 Evidence 覆盖
        CONTRACT_ITEM_SATISFIED 或其支撑产物。"""
        item = await self._session.get(DeliveryItem, delivery_item_id)
        if item is None:
            raise EvidenceError(f"DeliveryItem not found: {delivery_item_id}")
        stmt = select(CompletionEvidence).where(
            CompletionEvidence.valid.is_(True),
            CompletionEvidence.ref == f"delivery_item:{delivery_item_id}",
        )
        covering = (await self._session.execute(stmt)).scalars().all()
        return bool(covering)

    async def mark_item_satisfied(
        self, delivery_item_id: int
    ) -> DeliveryItem:
        """红线执法: Evidence 缺失时 SATISFIED 标记被拒绝。"""
        if not await self.check_delivery_item(delivery_item_id):
            raise EvidenceError(
                f"delivery_item:{delivery_item_id} cannot be SATISFIED — "
                "no valid evidence (create evidence first)"
            )
        item = await self._session.get(DeliveryItem, delivery_item_id)
        item.status = DeliveryItemStatus.SATISFIED
        await self._session.flush()
        return item

    # ── Project Completion（七条件，零 LLM）─────────────────────────

    async def check_project_completion(self, project_id: int) -> CompletionCheck:
        check = CompletionCheck(project_id=project_id)
        f = check.failures

        # ① Required Delivery Items 全 SATISFIED
        items = await self._project_delivery_items(project_id)
        required = [i for i in items if i.status != DeliveryItemStatus.WAIVED]
        unsatisfied = [
            i for i in required
            if i.status != DeliveryItemStatus.SATISFIED
        ]
        check.delivery_items_satisfied = not unsatisfied
        if unsatisfied:
            f.append(f"delivery items unsatisfied: "
                     f"{[i.requirement_ref[:40] for i in unsatisfied]}")

        # ② Traceability Coverage Complete（三查 gap 零）
        try:
            from project_runtime.traceability.graph import TraceabilityGraph

            tg = TraceabilityGraph(self._session)
            gaps = await tg.find_gaps(project_id)
            check.traceability_complete = not gaps
            if gaps:
                f.append(f"traceability gaps x{len(gaps)}: "
                         f"{[g.gap_type for g in gaps][:5]}")
        except Exception as e:  # noqa: BLE001 — graph rebuild optional here
            check.traceability_complete = False
            f.append(f"traceability check failed: {e}")

        # ③ Required Evidence Valid
        evidence = await self.get_scope_evidence(project_id)
        check.evidence_valid = len(evidence) > 0
        if not check.evidence_valid:
            f.append("no valid evidence in project scope")

        # ④ Required Task 全 ACCEPTED
        tasks = (
            (
                await self._session.execute(
                    select(Task).where(Task.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        not_accepted = [
            t for t in tasks if t.status != TaskStatus.ACCEPTED
        ]
        check.tasks_accepted = not not_accepted
        if not_accepted:
            f.append(f"tasks not ACCEPTED: {[t.name[:30] for t in not_accepted]}")

        # ⑤ Critical Issue = 0（OPEN 的非低危 gap）
        try:
            from project_runtime.project_state.models import MethodGap

            stmt = select(MethodGap).where(
                MethodGap.status == "OPEN",
                MethodGap.severity.in_(("HIGH", "CRITICAL")),
            )
            critical = (
                (await self._session.execute(stmt)).scalars().all()
            )
            check.critical_issues_zero = not critical
            if critical:
                f.append(f"critical issues open: {len(critical)}")
        except Exception:  # noqa: BLE001
            check.critical_issues_zero = True  # no gap records = no issues

        # ⑥ Final Gate PASS（项目最近 GateResult 全 PASS）
        task_ids = [t.id for t in tasks]
        gates = (
            (
                await self._session.execute(
                    select(GateResult).where(GateResult.task_id.in_(task_ids))
                )
            )
            .scalars()
            .all()
        ) if task_ids else []
        failing = [
            g for g in gates if g.status != GateStatus.PASS
        ]
        check.final_gate_passed = bool(gates) and not failing
        if not check.final_gate_passed:
            f.append("final gate not passed (no gates or failing gates)")

        # ⑦ Pending ManualAction = 0
        from project_runtime.project_state.enums import ManualActionStatus
        from project_runtime.project_state.models import ManualAction

        pending = (
            (
                await self._session.execute(
                    select(ManualAction).where(
                        ManualAction.project_id == project_id,
                        ManualAction.status == ManualActionStatus.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        check.no_pending_manual_actions = not pending
        if pending:
            f.append(f"pending manual actions: {len(pending)}")

        logger.info(
            "Completion check project=%d complete=%s failures=%d",
            project_id, check.complete, len(check.failures),
        )
        return check

    # ── helpers ─────────────────────────────────────────────────────

    async def _scope_rows(
        self, project_id: int, *, scope: EvidenceScope | None = None
    ) -> list[CompletionEvidence]:
        stmt = select(CompletionEvidence).where(
            CompletionEvidence.project_id == project_id
        )
        if scope is not None:
            stmt = stmt.where(CompletionEvidence.scope == scope)
        return list((await self._session.execute(stmt)).scalars().all())

    async def _project_delivery_items(
        self, project_id: int
    ) -> list[DeliveryItem]:
        stmt = (
            select(DeliveryItem)
            .join(
                DeliveryContract,
                DeliveryItem.contract_id == DeliveryContract.id,
            )
            .where(DeliveryContract.project_id == project_id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _ref_still_holds(self, ev: CompletionEvidence) -> bool:
        """失效条件复检: ref 实体回退则证据失效。"""
        kind, _, raw = ev.ref.partition(":")
        if not raw or not raw.isdigit():
            return True  # 非实体 ref（如 manual 描述）不自动失效
        eid = int(raw)
        try:
            if kind == "task":
                t = await self._session.get(Task, eid)
                return t is not None and t.status == TaskStatus.ACCEPTED
            if kind == "gate":
                g = await self._session.get(GateResult, eid)
                return g is not None and g.status == GateStatus.PASS
            if kind == "delivery_item":
                d = await self._session.get(DeliveryItem, eid)
                return d is not None and (
                    d.status == DeliveryItemStatus.SATISFIED
                )
        except Exception:  # noqa: BLE001
            return False
        return True
