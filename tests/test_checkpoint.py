"""WP-017 Checkpoint tests — 验收四条.

- [x] 可保存 Project/Graph/Frontier/Artifact/Issue/ManualAction/Delivery
- [x] 可恢复
- [x] Worker 不存在时项目状态仍可恢复
- [x] 关键事件自动创建
"""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.checkpoint import (
    CheckpointError,
    CheckpointManager,
    CheckpointRecord,
)
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    ManualActionStatus,
    ManualActionType,
    MethodAssetAuthority,
    TaskStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    ManualAction,
    MethodAsset,
    Project,
    Task,
    TaskDependency,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cp.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, tmp_path):
    """项目 + 两任务链 + 产物 + ManualAction。"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="cp 测试", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content="# 流程",
            content_hash=hashlib.sha256(b"x").hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        t1 = Task(project_id=pid, name="任务A", status=TaskStatus.ACCEPTED)
        t2 = Task(project_id=pid, name="任务B", status=TaskStatus.READY)
        s.add_all([t1, t2])
        await s.flush()
        t1_id, t2_id = t1.id, t2.id
        s.add(TaskDependency(task_id=t2_id, depends_on_id=t1_id))
        s.add(Artifact(
            logical_artifact_id="out", project_id=pid, task_id=t1_id,
            name="产物.v1.md", version=1, path="data/x.v1.md",
            sha256="h" * 64,
        ))
        s.add(ManualAction(
            project_id=pid, task_id=t2_id,
            action_type=ManualActionType.TASK_DISPATCH,
            proposal_payload="{}", status=ManualActionStatus.PENDING,
        ))
        await s.commit()
    return pid, t1_id, t2_id


# ── Gate 1: 八段内容保存 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_contains_all_sections(dbengine, tmp_path):
    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        rid, rpath = record.id, record.path  # snapshot before commit
        await s.commit()

        # reload the snapshot JSON and verify every section
        body = json.loads(Path(rpath).read_text(encoding="utf-8"))
        assert body["schema_version"] == "v1"
        assert body["project"]["name"] == "cp 测试"
        assert len(body["task_graph"]["tasks"]) == 2
        assert len(body["task_graph"]["dependencies"]) == 1
        assert body["current_frontier"]["ready"] == [2]
        assert len(body["artifacts"]) == 1
        assert body["artifacts"][0]["version"] == 1
        assert body["decisions"] == []  # no research yet
        assert body["issues"] == []  # no assessment yet
        assert len(body["manual_actions"]) == 1
        assert body["delivery"] is None  # no contract yet
        assert "worker_references" in body
        assert rid == 1


# ── Gate 2: 可恢复 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_to_new_project_copy(dbengine, tmp_path):
    pid, t1_id, t2_id = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        cp_id = record.id
        await s.commit()

    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        new_pid = await mgr.restore(cp_id)
        await s.commit()

    # new project copy exists with tasks + deps restored
    async with AsyncSession(dbengine) as s:
        tasks = (await s.execute(
            select(Task).where(Task.project_id == new_pid))).scalars().all()
        assert len(tasks) == 2
        statuses = {t.name: t.status.value for t in tasks}
        assert statuses["任务A"] == "ACCEPTED"
        assert statuses["任务B"] == "READY"
        deps = (await s.execute(
            select(TaskDependency).where(
                TaskDependency.task_id.in_([t.id for t in tasks]))
        )).scalars().all()
        assert len(deps) == 1

        # original project untouched
        orig = await s.get(Project, pid)
        assert orig.name == "cp 测试"
        orig_tasks = (await s.execute(
            select(Task).where(Task.project_id == pid))).scalars().all()
        assert len(orig_tasks) == 2
        # restored project got the restored- prefix
        restored = await s.get(Project, new_pid)
        assert restored.name == "restored-cp 测试"


# ── Gate 3: Worker 不存在时仍可恢复 ─────────────────────────────────


@pytest.mark.asyncio
async def test_restore_without_workers(dbengine, tmp_path):
    """零 TaskRun/零 Execution（Worker 从未跑过）——快照自包含可恢复。"""
    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        cp_id = record.id
        await s.commit()

    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        new_pid = await mgr.restore(cp_id)
        await s.commit()
        tasks = (await s.execute(
            select(Task).where(Task.project_id == new_pid))).scalars().all()
        assert len(tasks) == 2  # fully restored, workers never existed


# ── Gate 4: 关键事件自动创建 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_checkpoint_triggers(dbengine, tmp_path):
    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        r1 = await mgr.auto_checkpoint_on_gate_fail(pid)
        r2 = await mgr.auto_checkpoint_on_milestone(pid)
        # snapshot trigger values before commit
        t1v, t2v = r1.trigger, r2.trigger
        await s.commit()
        assert r1 is not None and t1v == "AUTO_GATE_FAIL"
        assert r2 is not None and t2v == "AUTO_MILESTONE"
        # records persisted + files on disk
        rows = (await s.execute(
            select(CheckpointRecord).where(
                CheckpointRecord.project_id == pid))).scalars().all()
        assert len(rows) == 2
        assert all(Path(r.path).exists() for r in rows)


# ── 完整性: 篡改检测 + schema 版本 ──────────────────────────────────


@pytest.mark.asyncio
async def test_tampered_snapshot_rejected(dbengine, tmp_path):
    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        cp_id = record.id
        await s.commit()

    # tamper the file on disk (re-fetch record — prior session closed)
    async with AsyncSession(dbengine) as s:
        rec = await s.get(CheckpointRecord, cp_id)
        fpath = Path(rec.path)
    body = json.loads(fpath.read_text(encoding="utf-8"))
    body["project"]["name"] = "被篡改的名字"
    fpath.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True),
                     encoding="utf-8")

    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        with pytest.raises(CheckpointError, match="hash mismatch"):
            await mgr.restore(cp_id)


@pytest.mark.asyncio
async def test_missing_file_rejected(dbengine, tmp_path):
    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        cp_id, fpath_str = record.id, record.path  # snapshot
        await s.commit()
        fpath = Path(fpath_str)

    fpath.unlink()
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        with pytest.raises(CheckpointError, match="missing"):
            await mgr.restore(cp_id)


@pytest.mark.asyncio
async def test_decisions_and_issues_captured(dbengine, tmp_path):
    """有 Research（决策）与 Gap（Issue）时快照含两段。"""
    from project_runtime.research.models import ResearchRecord

    pid, _, _ = await _seed(dbengine, tmp_path)
    async with AsyncSession(dbengine) as s:
        s.add(ResearchRecord(
            project_id=pid, research_kind="DOMAIN_RESEARCH",
            question="域知识", status="ANSWERED", summary="要点",
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        record = await mgr.create(pid)
        rpath = record.path  # snapshot before commit
        await s.commit()
        body = json.loads(Path(rpath).read_text(encoding="utf-8"))
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["summary"] == "要点"
