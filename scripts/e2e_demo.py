"""D18 E2E — single-command demo: fresh DB from zero, full chain + skill branch.

Usage:
    uv run python scripts/e2e_demo.py

Exits 0 on PASS, 1 on FAIL. No fake state, no manual DB edits.
"""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "e2e"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="paa-e2e-")
    tmp_path = Path(tmp)
    db_file = tmp_path / "e2e.db"
    # NOTE: body below was originally a `with tempfile.TemporaryDirectory()`
    # block; converted to manual cleanup because Windows holds SQLite WAL/shm
    # file handles past engine.dispose() and cleanup raised PermissionError.
    if True:

        # 1. Fresh DB from zero via alembic (PAA_DB_PATH targets the temp DB;
        # no fallback to create_all — if migration fails, the demo fails).
        step("1. Fresh DB migration (alembic upgrade head)")
        import os

        env = {**os.environ, "PAA_DB_PATH": str(db_file)}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr[-2000:])
            print("E2E FAIL: alembic upgrade head failed on fresh DB")
            return 1
        # Verify the temp DB (not the dev DB) was migrated
        import sqlite3

        conn = sqlite3.connect(db_file)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert "skill_definition" in tables and "project" in tables, (
            f"migration incomplete: {len(tables)} tables"
        )
        print(f"  migrated temp DB: {len(tables)} tables")

        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        env_db = f"sqlite+aiosqlite:///{db_file}"
        engine = create_async_engine(env_db, echo=False)

        async with AsyncSession(engine) as session:
            from project_runtime.artifact.manager import ArtifactManager
            from project_runtime.gap.classifier import GapClassifier
            from project_runtime.method_asset.importer import MethodAssetImporter
            from project_runtime.planner.planner import DynamicPlanner
            from project_runtime.playbook.builder import PlaybookBuilder
            from project_runtime.procedure.normalizer import ProcedureNormalizer
            from project_runtime.project_state.enums import (
                AutomationMode,
                GateStatus,
                ProjectStatus,
                TaskStatus,
            )
            from project_runtime.project_state.models import Project, Task
            from project_runtime.readiness.assessor import MethodReadinessAssessor
            from project_runtime.reviewer.gate import GateCore
            from project_runtime.scheduler.scheduler import Scheduler
            from project_runtime.skills import (
                ResolutionStatus,
                SkillLoader,
                SkillRegistry,
                SkillRequirement,
                SkillResolver,
                SkillScopeType,
                SkillType,
            )
            from project_runtime.skills.bindings import SkillBindingManager
            from project_runtime.skills.importer import SkillPackageImporter
            from project_runtime.task_graph.manager import TaskGraphManager
            from project_runtime.worker.adapter import MockWorkerAdapter, WorkPackage

            step("2. Main chain: method asset → playbook → task graph")
            assert await SkillRegistry(session).list() == []  # L1 empty registry

            project = Project(
                name="公文助手", automation_mode=AutomationMode.FULL_AUTO,
                status=ProjectStatus.ACTIVE,
            )
            session.add(project)
            await session.flush()
            project_id = project.id  # snapshot — worker commits expire ORM objects

            asset = await MethodAssetImporter(session).import_file(
                project_id, FIXTURES / "method" / "sop.md"
            )
            model = await ProcedureNormalizer(session).normalize(project.id)
            assessment = await MethodReadinessAssessor(session).assess(
                project.id, model.id
            )
            gaps = await GapClassifier(session).classify(assessment.id)
            playbook = await PlaybookBuilder(session).build(project.id)
            await DynamicPlanner(session).plan(playbook)
            print(f"  asset={asset.id} model={model.id} gaps={len(gaps)} "
                  f"playbook={playbook.id}")

            step("3. Skill branch: import → bind → resolve → load")
            await SkillPackageImporter(session).import_directory(
                str(FIXTURES / "skill_pkg")
            )
            bnd = SkillBindingManager(session)
            await bnd.bind(
                project.id, SkillScopeType.TASK_TYPE, "需求",
                "e2e-mock-skill", "1.0.0",
            )
            r = await SkillResolver(session).resolve(
                SkillRequirement(skill_type=SkillType.TASK_EXECUTION, task_type="需求"),
                project_id=project.id,
            )
            assert r.status == ResolutionStatus.RESOLVED, r.status
            ctx = await SkillLoader(session).load(
                r.resolved_skill.skill_id, r.resolved_skill.version,
                expected_hash=r.resolved_skill.content_hash,
            )
            print(f"  skill={ctx.skill_id}@{ctx.skill_version} hash={ctx.skill_content_hash[:12]}")

            step("4. Dispatch → execute → artifact → gate → accepted")
            mgr = TaskGraphManager(session)
            artifact_mgr = ArtifactManager(session, workspace_root=str(tmp_path / "tasks"))
            worker = MockWorkerAdapter(session, artifact_mgr=artifact_mgr)
            scheduler = Scheduler(session, worker=worker, max_workers=1)

            tasks = await mgr.list_tasks(project.id)
            for t in tasks:
                try:
                    await mgr.promote_to_ready(t.id)
                except Exception:
                    pass

            decisions = await scheduler.dispatch_once(project_id)
            dispatched = [d for d in decisions if d.action == "DISPATCHED"]
            assert dispatched, "nothing dispatched"

            from sqlalchemy import select as _sel

            async def _task_status(tid):
                return (
                    await session.execute(_sel(Task.status).where(Task.id == tid))
                ).scalar_one()

            gate = GateCore(session)
            accepted = 0
            for d in dispatched:
                # worker short-tx commits (WP-041) expire ORM objects — scalar read
                if await _task_status(d.task_id) == TaskStatus.EXECUTION_DONE:
                    g = await gate.evaluate(d.task_id)
                    if g.status == GateStatus.PASS:
                        accepted += 1
            assert accepted >= 1
            print(f"  dispatched={len(dispatched)} accepted={accepted}")

            step("5. Skill-equipped task execution (audit)")
            t2 = await mgr.create_task(
                Task(project_id=project_id, name="需求梳理")
            )
            await mgr.promote_to_ready(t2.id)
            t2_id = t2.id  # snapshot before worker short-tx commit expires ORM objects
            t2_name = t2.name
            result = await worker.assign(
                WorkPackage(task_id=t2_id, task_name=t2_name, skill_context=ctx)
            )
            assert result.actual_skill_id == "e2e-mock-skill"
            assert result.actual_skill_version == "1.0.0"
            assert await _task_status(t2_id) == TaskStatus.EXECUTION_DONE  # not ACCEPTED by skill
            g2 = await gate.evaluate(t2_id)
            assert g2.status == GateStatus.PASS
            print(f"  skill task accepted via gate; audit={result.actual_skill_id}")

        await engine.dispose()

        # Windows: release WAL/shm file handles before TemporaryDirectory cleanup
        import sqlite3 as _sq3

        try:
            _c = _sq3.connect(str(db_file))
            _c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _c.close()
        except Exception:
            pass

    step("E2E PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
