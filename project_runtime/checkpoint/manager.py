"""CheckpointManager (WP-017) — 项目快照与恢复.

快照八段内容（规范）:
  Project / Task Graph / Current Frontier / Artifact Versions /
  Decision（Research）/ Issue（Gap）/ ManualAction·HumanDecision /
  Delivery Contract + Worker References

自动触发: Gate FAIL（REWORK 落定）/ 里程碑（项目阶段迁移）——advisory、
best-effort，绝不打断主链。

恢复语义: 快照恢复到**新项目副本**（名字加 restored- 前缀）——原项目
状态永不覆盖；Worker 不存在（无 TaskRun/Execution 引用）时同样可恢复
（快照自包含，不依赖外部进程）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.checkpoint.models import CheckpointRecord
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import (
    Artifact,
    ManualAction,
    MethodGap,
    Project,
    Task,
    TaskDependency,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "v1"
CHECKPOINT_ROOT = Path("data/checkpoints")


class CheckpointError(Exception):
    pass


class CheckpointManager:
    def __init__(self, session: AsyncSession, *, root: Path | None = None):
        self._session = session
        self._root = root or CHECKPOINT_ROOT

    # ── create ──────────────────────────────────────────────────────

    async def create(
        self, project_id: int, *, trigger: str = "MANUAL"
    ) -> CheckpointRecord:
        """收集八段内容 → JSON 快照落盘 + 元数据行。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise CheckpointError(f"Project not found: {project_id}")

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "trigger": trigger,
            "created_at": _now_iso(),
            "project": self._project_view(project),
            "task_graph": await self._task_graph(project_id),
            "current_frontier": await self._frontier(project_id),
            "artifacts": await self._artifacts(project_id),
            "decisions": await self._decisions(project_id),
            "issues": await self._issues(project_id),
            "manual_actions": await self._manual_actions(project_id),
            "delivery": await self._delivery(project_id),
            "worker_references": await self._worker_references(project_id),
        }

        body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{project_id}" / f"cp_{_ts()}_{trigger.lower()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

        record = CheckpointRecord(
            project_id=project_id,
            trigger=trigger,
            schema_version=SCHEMA_VERSION,
            path=str(path),
            content_hash=content_hash,
            summary=(
                f"tasks={len(snapshot['task_graph']['tasks'])} "
                f"artifacts={len(snapshot['artifacts'])} "
                f"gaps={len(snapshot['issues'])}"
            ),
        )
        self._session.add(record)
        await self._session.flush()
        logger.info(
            "Checkpoint created: project=%d trigger=%s path=%s",
            project_id, trigger, path,
        )
        return record

    # ── restore ─────────────────────────────────────────────────────

    async def restore(self, checkpoint_id: int) -> int:
        """快照 → 新项目副本（原项目不动）。返回新 project_id。"""
        record = await self._session.get(CheckpointRecord, checkpoint_id)
        if record is None:
            raise CheckpointError(f"Checkpoint not found: {checkpoint_id}")
        path = Path(record.path)
        if not path.exists():
            raise CheckpointError(
                f"checkpoint file missing: {path}（注册表与磁盘不一致）"
            )
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("schema_version") != record.schema_version:
            raise CheckpointError(
                f"schema mismatch: file={snapshot.get('schema_version')} "
                f"record={record.schema_version}"
            )
        # 完整性校验（防篡改/截断）
        body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if record.content_hash and hashlib.sha256(
            body.encode("utf-8")
        ).hexdigest() != record.content_hash:
            raise CheckpointError("content hash mismatch — snapshot corrupted")

        src = snapshot["project"]
        restored = Project(
            name=f"restored-{src['name']}",
            goal=src.get("goal"),
            automation_mode=src["automation_mode"],
            status=src["status"],
        )
        self._session.add(restored)
        await self._session.flush()
        new_pid = restored.id

        # Task Graph（含依赖边）—— 状态按快照原样重建（fixture 直建，
        # 不走 FSM：恢复的是已验证过的历史状态）
        id_map: dict[int, int] = {}
        for t in snapshot["task_graph"]["tasks"]:
            nt = Task(
                project_id=new_pid, name=t["name"],
                status=TaskStatus(t["status"]),
                playbook_ref=t.get("playbook_ref"),
            )
            self._session.add(nt)
            await self._session.flush()
            id_map[t["id"]] = nt.id
        for dep in snapshot["task_graph"]["dependencies"]:
            self._session.add(TaskDependency(
                task_id=id_map[dep["task_id"]],
                depends_on_id=id_map[dep["depends_on_id"]],
                dependency_type=dep.get("dependency_type", "HARD"),
            ))
        await self._session.flush()
        logger.info(
            "Checkpoint restored: cp=%d → new project %d (%d tasks)",
            checkpoint_id, new_pid, len(id_map),
        )
        return new_pid

    # ── auto triggers (advisory, best-effort) ───────────────────────

    async def auto_checkpoint_on_gate_fail(
        self, project_id: int
    ) -> CheckpointRecord | None:
        try:
            return await self.create(
                project_id, trigger="AUTO_GATE_FAIL"
            )
        except Exception:  # noqa: BLE001 — never blocks the main chain
            logger.debug("auto checkpoint failed", exc_info=True)
            return None

    async def auto_checkpoint_on_milestone(
        self, project_id: int
    ) -> CheckpointRecord | None:
        try:
            return await self.create(
                project_id, trigger="AUTO_MILESTONE"
            )
        except Exception:  # noqa: BLE001
            logger.debug("auto milestone checkpoint failed", exc_info=True)
            return None

    # ── collectors (八段) ────────────────────────────────────────────

    @staticmethod
    def _project_view(project: Project) -> dict:
        return {
            "id": project.id,
            "name": project.name,
            "goal": project.goal,
            "status": project.status.value,
            "automation_mode": project.automation_mode.value,
        }

    async def _task_graph(self, project_id: int) -> dict:
        tasks = (
            (
                await self._session.execute(
                    select(Task).where(Task.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        task_ids = [t.id for t in tasks]
        deps = []
        if task_ids:
            deps = (
                (
                    await self._session.execute(
                        select(TaskDependency).where(
                            TaskDependency.task_id.in_(task_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
        return {
            "tasks": [
                {
                    "id": t.id, "name": t.name, "status": t.status.value,
                    "playbook_ref": t.playbook_ref,
                }
                for t in tasks
            ],
            "dependencies": [
                {
                    "task_id": d.task_id, "depends_on_id": d.depends_on_id,
                    "dependency_type": d.dependency_type.value,
                }
                for d in deps
            ],
        }

    async def _frontier(self, project_id: int) -> dict:
        """Current Frontier: 可推进边界（READY 集 + 被阻塞统计）。"""
        tasks = (
            (
                await self._session.execute(
                    select(Task).where(Task.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        ready = [t.id for t in tasks if t.status == TaskStatus.READY]
        blocked = sum(1 for t in tasks if t.status == TaskStatus.CREATED)
        return {"ready": ready, "blocked": blocked,
                "total": len(tasks)}

    async def _artifacts(self, project_id: int) -> list[dict]:
        rows = (
            (
                await self._session.execute(
                    select(Artifact).where(Artifact.project_id == project_id)
                    .order_by(Artifact.logical_artifact_id, Artifact.version)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": a.id, "logical_id": a.logical_artifact_id,
                "name": a.name, "version": a.version, "sha256": a.sha256,
                "task_id": a.task_id,
            }
            for a in rows
        ]

    async def _decisions(self, project_id: int) -> list[dict]:
        """决策记录 = ANSWERED Research（WP-029 可复用研究资产）。"""
        from project_runtime.research.models import ResearchRecord

        rows = (
            (
                await self._session.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "research_id": r.id, "kind": r.research_kind,
                "question": r.question, "status": r.status,
                "summary": r.summary,
            }
            for r in rows
        ]

    async def _issues(self, project_id: int) -> list[dict]:
        """Issue = MethodGap（含已解决——历史完整）。"""
        rows = (
            (
                await self._session.execute(
                    select(MethodGap).where(
                        MethodGap.assessment_id.in_(
                            select(
                                __import__(
                                    "project_runtime.project_state.models",
                                    fromlist=["ProjectReadinessAssessment"],
                                ).ProjectReadinessAssessment.id
                            ).where(
                                __import__(
                                    "project_runtime.project_state.models",
                                    fromlist=["ProjectReadinessAssessment"],
                                ).ProjectReadinessAssessment.project_id
                                == project_id
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "gap_id": g.id, "gap_type": g.gap_type.value,
                "description": g.description, "status": g.status,
                "resolution_strategy": g.resolution_strategy,
            }
            for g in rows
        ]

    async def _manual_actions(self, project_id: int) -> list[dict]:
        rows = (
            (
                await self._session.execute(
                    select(ManualAction).where(
                        ManualAction.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": m.id, "action_type": m.action_type.value,
                "status": m.status.value, "task_id": m.task_id,
                "confirmed_by": m.confirmed_by,
            }
            for m in rows
        ]

    async def _delivery(self, project_id: int) -> dict | None:
        from project_runtime.project_state.models import DeliveryContract

        stmt = (
            select(DeliveryContract)
            .where(DeliveryContract.project_id == project_id)
            .order_by(DeliveryContract.id.desc())
            .limit(1)
        )
        contract = (await self._session.execute(stmt)).scalar_one_or_none()
        if contract is None:
            return None
        return {
            "contract_id": contract.id, "version": contract.version,
            "status": contract.status.value,
            "items": json.loads(contract.items or "[]"),
        }

    async def _worker_references(self, project_id: int) -> dict:
        """Worker 引用（TaskRun/Execution 计数——恢复不依赖它们存在）。"""
        from project_runtime.project_state.models import TaskRun

        rows = (
            (
                await self._session.execute(
                    select(TaskRun.id).where(
                        TaskRun.task_id.in_(
                            select(Task.id).where(
                                Task.project_id == project_id
                            )
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        return {"task_runs": len(rows),
                "note": "references only — restore never requires workers"}


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ts() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
