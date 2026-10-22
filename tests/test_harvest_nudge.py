"""V1.5a-1 序6/7 测试: harvest 产物登记 + 软提醒一次性。"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.project_state.base import Base


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/h.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng, tmp_path
    await eng.dispose()


class TestBatchRegister:
    @pytest.mark.asyncio
    async def test_harvest_registers_new_files(self, dbengine, monkeypatch):
        """快照 diff 的新增文件 → 全量登记（含子目录/代码文件）。"""
        eng, tmp = dbengine
        monkeypatch.chdir(tmp)
        ws = tmp / "data/tasks/5"
        (ws / "src").mkdir(parents=True)
        (ws / "src/main.py").write_text("print('hi')", encoding="utf-8")
        (ws / "tests").mkdir()
        (ws / "tests/test_x.py").write_text("def test_x(): pass",
                                            encoding="utf-8")
        (ws / "README.md").write_text("# T", encoding="utf-8")

        from project_runtime.artifact.manager import ArtifactManager
        from project_runtime.project_state.models import Artifact

        async with AsyncSession(eng) as s:
            mgr = ArtifactManager(s, workspace_root="data/tasks/5")
            files = [ws / "src/main.py", ws / "tests/test_x.py",
                     ws / "README.md"]
            registered = await mgr.batch_register_from_workspace(
                project_id=1, task_id=5, files=files,
            )
            await s.commit()
            assert len(registered) == 3
        async with AsyncSession(eng) as s:
            rows = (
                await s.execute(
                    select(Artifact).where(Artifact.task_id == 5)
                )
            ).scalars().all()
            assert len(rows) == 3
            lids = {r.logical_artifact_id for r in rows}
            # 文件树编码在 logical_id（路径结构）
            assert any("src/main.py" in lid for lid in lids)
            assert any("tests/test_x.py" in lid for lid in lids)
            assert any("README.md" in lid for lid in lids)

    @pytest.mark.asyncio
    async def test_harvest_idempotent(self, dbengine, monkeypatch):
        """同文件同 hash 二次登记跳过（幂等）。"""
        eng, tmp = dbengine
        monkeypatch.chdir(tmp)
        ws = tmp / "data/tasks/5"
        ws.mkdir(parents=True)
        f = ws / "a.py"
        f.write_text("x = 1", encoding="utf-8")

        from project_runtime.artifact.manager import ArtifactManager

        async with AsyncSession(eng) as s:
            mgr = ArtifactManager(s, workspace_root="data/tasks/5")
            r1 = await mgr.batch_register_from_workspace(
                1, 5, [f])
            await s.commit()
            assert len(r1) == 1
        async with AsyncSession(eng) as s:
            mgr = ArtifactManager(s, workspace_root="data/tasks/5")
            r2 = await mgr.batch_register_from_workspace(
                1, 5, [f])
            await s.commit()
            assert len(r2) == 0  # 同 hash 跳过


class TestSoftNudge:
    def _worker(self, tmp, monkeypatch, ws_files: dict):
        """构造 worker + 工作区文件。"""
        monkeypatch.chdir(tmp)
        ws = tmp / "data/tasks/7"
        ws.mkdir(parents=True, exist_ok=True)
        for rel, content in ws_files.items():
            fp = ws / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
        w._current_task_id = 7
        w._nudged_tasks = set()

        class _Mgr:
            _workspace_root = "data/tasks"
        w._artifact_mgr = _Mgr()
        return w

    @pytest.mark.asyncio
    async def test_nudge_when_code_no_tests(self, dbengine, monkeypatch):
        """code_stage + 有代码 + 零测试 → 提醒附加（一次性）。"""
        eng, tmp = dbengine
        w = self._worker(tmp, monkeypatch, {"src/main.py": "print(1)"})

        class _WP:
            task_id = 7
            code_stage = True
        out = await w._soft_nudge_tests(_WP(), "原产出")
        assert "测试文件" in out and "系统提醒" in out
        # 第二次放行（一次性）
        out2 = await w._soft_nudge_tests(_WP(), "原产出")
        assert out2 == "原产出"

    @pytest.mark.asyncio
    async def test_no_nudge_when_tests_exist(self, dbengine, monkeypatch):
        eng, tmp = dbengine
        w = self._worker(tmp, monkeypatch, {
            "src/main.py": "print(1)",
            "tests/test_a.py": "def test_a(): pass",
        })

        class _WP:
            task_id = 7
            code_stage = True
        out = await w._soft_nudge_tests(_WP(), "原产出")
        assert out == "原产出"  # 有测试——不提醒

    @pytest.mark.asyncio
    async def test_no_nudge_when_not_code_stage(self, dbengine, monkeypatch):
        eng, tmp = dbengine
        w = self._worker(tmp, monkeypatch, {"src/main.py": "print(1)"})

        class _WP:
            task_id = 7
            code_stage = False
        out = await w._soft_nudge_tests(_WP(), "原产出")
        assert out == "原产出"  # 非 code_stage——不提醒


class TestCodeStagePassthrough:
    def test_planner_ref_carries_code_stage(self):
        """playbook_ref 含 code_stage → _code_stage_of 解析。"""
        import json

        from project_runtime.execution.service import _code_stage_of

        ref = json.dumps({"code_stage": True, "stage_outputs": ["README"]})
        assert _code_stage_of(ref) is True
        assert _code_stage_of(json.dumps({"code_stage": False})) is False
        assert _code_stage_of(None) is False
        assert _code_stage_of("not json") is False
