"""Project deletion service — 级联删除项目的全部数据.

删除范围（三层数据 + 磁盘文件）:
  1. project_id 直连表（26 张）
  2. 经父表间接关联表（task_dependency/task_run/gate_result 经 task；
     procedure_node 经 model；method_gap 经 assessment；
     delivery_item 经 contract）
  3. 磁盘: data/tasks/{project_id}/（产物文件）+ data/checkpoints/{project_id}/

安全: 全部在同一事务内（SQLite 单写者原子性）；删除前返回统计预览
（dry_run），实际删除返回已删计数。正在 RUNNING 的执行会被级联清除
（含 execution 行——后台 runner 对已删对象 best-effort 失败不抛）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.project_state.models import Project

logger = logging.getLogger(__name__)

# project_id 直连表（删除序无关——同事务）
_DIRECT_TABLES = [
    "runtime_event",
    "execution",
    "manual_action",
    "artifact",
    "task",
    "skill_binding",
    "method_asset",
    "project_playbook",
    "project_readiness_assessment",
    "project_procedure_model",
    "checkpoint_record",
    "completion_evidence",
    "trace_edge",
    "trace_node",
    "dynamic_artifact_contract",
    "research_record",
    "project_understanding",
    "project_summary",
    "retrieval_document",
    "delivery_contract",
    "checkpoint",
    "decision",
    "issue",
    "event",
    "human_decision",
    "constraint",
    # 20号规范批次1 表（V1.4e 补——此前缺失致删除事务 FK 断链回滚）
    "agent_instance",
]

_INDIRECT = {
    # table: (父表链 join 条件)
    "task_dependency": 'task_id IN (SELECT id FROM "task" WHERE project_id = :pid)',
    "task_run": 'task_id IN (SELECT id FROM "task" WHERE project_id = :pid)',
    "gate_result": 'task_id IN (SELECT id FROM "task" WHERE project_id = :pid)',
    "procedure_node": (
        'model_id IN (SELECT id FROM "project_procedure_model" '
        'WHERE project_id = :pid)'
    ),
    "method_gap": (
        'assessment_id IN (SELECT id FROM "project_readiness_assessment" '
        'WHERE project_id = :pid)'
    ),
    "delivery_item": (
        'contract_id IN (SELECT id FROM "delivery_contract" '
        'WHERE project_id = :pid)'
    ),
    # 20号规范: ACP 消息经 agent_instance 间接引用 project——
    # from/to 两向都清（任一端是本项目实例即删）
    "acp_message": (
        'from_agent_instance_id IN '
        '(SELECT id FROM agent_instance WHERE project_id = :pid) '
        'OR to_agent_instance_id IN '
        '(SELECT id FROM agent_instance WHERE project_id = :pid)'
    ),
}


class ProjectDeletionError(Exception):
    pass


class ProjectDeletionService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def preview(self, project_id: int) -> dict:
        """删除预览（计数，不执行）。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ProjectDeletionError(f"Project not found: {project_id}")
        counts = {"project": {project_id: project.name}}
        for t in _DIRECT_TABLES:
            n = await self._count_direct(t, project_id)
            if n:
                counts[t] = n
        for t, cond in _INDIRECT.items():
            n = await self._count_indirect(t, cond, project_id)
            if n:
                counts[t] = n
        disk = self._disk_paths(project_id)
        counts["disk"] = {
            "tasks_dir": str(disk[0]),
            "tasks_exists": disk[0].exists(),
            "checkpoints_dir": str(disk[1]),
            "checkpoints_exists": disk[1].exists(),
        }
        return counts

    async def delete(self, project_id: int) -> dict:
        """级联删除（单事务）+ 磁盘清理。返回已删计数。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ProjectDeletionError(f"Project not found: {project_id}")
        pname = project.name

        deleted: dict[str, int] = {}
        # 0. FK 断链防护: 任意事件行若 execution_id 指向本项目的
        # execution（可能因历史 bug 挂在别的 project_id 下），先清掉
        # ——否则删 execution 时 FOREIGN KEY constraint 崩事务
        n = await self._session.execute(
            text(
                'DELETE FROM "runtime_event" WHERE execution_id IN '
                '(SELECT id FROM "execution" WHERE project_id = :pid)'
            ).bindparams(pid=project_id)
        )
        if n.rowcount:
            deleted["runtime_event(dangling_refs)"] = n.rowcount
        # 0b. 同理: 事件 task_id 指向本项目 task 的跨项目事件
        n2 = await self._session.execute(
            text(
                'DELETE FROM "runtime_event" WHERE task_id IN '
                '(SELECT id FROM "task" WHERE project_id = :pid)'
            ).bindparams(pid=project_id)
        )
        if n2.rowcount:
            deleted["runtime_event(dangling_task_refs)"] = n2.rowcount
        # 1. 间接表先删（父表还在，子查询有效）
        for t, cond in _INDIRECT.items():
            n = await self._session.execute(
                text(f'DELETE FROM "{t}" WHERE {cond}').bindparams(pid=project_id)
            )
            if n.rowcount:
                deleted[t] = n.rowcount
        # 2. 直连表
        for t in _DIRECT_TABLES:
            n = await self._session.execute(
                text(f'DELETE FROM "{t}" WHERE project_id = :pid').bindparams(
                    pid=project_id
                )
            )
            if n.rowcount:
                deleted[t] = n.rowcount
        # 3. project 本体
        await self._session.delete(project)
        await self._session.flush()
        deleted["project"] = 1

        # 4. 磁盘（事务外 best-effort——DB 已删，文件清理失败不回滚）
        disk = self._disk_paths(project_id)
        removed = []
        for d in disk:
            if d.exists():
                import shutil

                shutil.rmtree(d, ignore_errors=True)
                removed.append(str(d))

        await self._session.commit()
        logger.info(
            "Project %d (%s) deleted: %s + disk %s",
            project_id, pname, deleted, removed,
        )
        return {"project_id": project_id, "name": pname,
                "tables": deleted, "disk_removed": removed}

    # ── helpers ─────────────────────────────────────────────────────

    async def _count_direct(self, table: str, pid: int) -> int:
        try:
            r = await self._session.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE project_id = :pid')
                .bindparams(pid=pid)
            )
            return int(r.scalar_one())
        except Exception:  # noqa: BLE001 — 表可能不存在于旧库
            return 0

    async def _count_indirect(self, table: str, cond: str, pid: int) -> int:
        try:
            r = await self._session.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE {cond}').bindparams(
                    pid=pid
                )
            )
            return int(r.scalar_one())
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _disk_paths(project_id: int) -> tuple[Path, Path]:
        return (
            Path("data/tasks") / str(project_id),
            Path("data/checkpoints") / str(project_id),
        )
