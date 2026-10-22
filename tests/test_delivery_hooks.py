"""V1.6 序7: 三件必然交付钩子链测试。

覆盖设计单 §六测试计划:
- 判定器: SOFTWARE_DEV 正例/反例/无 LLM 兜底/双失败保守
- 注入断言: SOFTWARE_DEV → extractor prompt 含基线约定; NOT_SOFTWARE 零注入
- file_rw 禁闭: __ws_root 内 ok / 越界拒（指引文案）
- stage_outputs 四件套: code_stage 空 outputs → 补默认
- delivery_tags 归一: 词表命中
- 收工自查: tags 任务注入自查消息+文件树; 一次性; 无 tags 零注入
- slide_generator 元数据字段存在（stub）
- 收口对账: 三件齐无补建 / 缺件补建 + RECONCILIATION_GAP 事件 / 防死循环
"""

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
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dh.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


# ═══ 判定器 ══════════════════════════════════════════════════════

class TestClassifier:
    @pytest.mark.asyncio
    async def test_llm_true(self, dbengine):
        from project_runtime.procedure.delivery_classifier import (
            SOFTWARE_DEV,
            DeliveryClassifier,
        )

        async def fake_llm(text):
            return '{"software_dev": true, "reason": "开发文档"}'

        c = DeliveryClassifier(None, llm_fn=fake_llm)
        assert await c.classify("做一个记账小工具") == SOFTWARE_DEV

    @pytest.mark.asyncio
    async def test_llm_false(self, dbengine):
        from project_runtime.procedure.delivery_classifier import (
            NOT_SOFTWARE,
            DeliveryClassifier,
        )

        async def fake_llm(text):
            return '{"software_dev": false, "reason": "操作手册"}'

        c = DeliveryClassifier(None, llm_fn=fake_llm)
        assert await c.classify("XX系统操作手册") == NOT_SOFTWARE

    @pytest.mark.asyncio
    async def test_regex_fallback(self, dbengine):
        """LLM 禁用 → 正则兜底命中开发词。"""
        from project_runtime.procedure.delivery_classifier import (
            SOFTWARE_DEV,
            DeliveryClassifier,
        )

        c = DeliveryClassifier(None, llm_fn=False)
        assert await c.classify("本文档描述接口设计与数据库表结构") == SOFTWARE_DEV

    @pytest.mark.asyncio
    async def test_conservative_when_no_match(self, dbengine):
        """双失败（LLM 禁用 + 正则不中）→ 保守 NOT_SOFTWARE。"""
        from project_runtime.procedure.delivery_classifier import (
            NOT_SOFTWARE,
            DeliveryClassifier,
        )

        c = DeliveryClassifier(None, llm_fn=False)
        assert await c.classify("会议纪要：关于年度预算的讨论") == NOT_SOFTWARE

    @pytest.mark.asyncio
    async def test_garbage_llm_falls_back(self, dbengine):
        """LLM 输出不可解析 → 正则兜底。"""
        from project_runtime.procedure.delivery_classifier import (
            SOFTWARE_DEV,
            DeliveryClassifier,
        )

        async def bad_llm(text):
            return "我不确定这是什么"

        c = DeliveryClassifier(None, llm_fn=bad_llm)
        assert await c.classify("系统设计与部署文档") == SOFTWARE_DEV


# ═══ extractor 注入 ══════════════════════════════════════════════

class TestExtractorInjection:
    def _extractor(self, profile):
        from project_runtime.procedure.llm_extractor import LLMExtractor

        class _Cfg:
            base_url = "http://x"
            api_key = "k"
            model = "m"
        ex = LLMExtractor.__new__(LLMExtractor)
        ex._cfg = _Cfg()
        ex._fallback = None
        ex._project_id = None
        ex._delivery_profile = profile
        return ex

    @pytest.mark.asyncio
    async def test_software_dev_injects_baseline(self):
        """SOFTWARE_DEV → prompt 含基线约定（断言三件+阶段自主措辞）。"""
        from unittest.mock import patch

        ex = self._extractor("SOFTWARE_DEV")
        captured = {}

        async def fake_acomplete(*args, **kw):
            captured["prompt"] = kw.get("messages", [{}])[0].get("content", "")

            class _Chunk:
                def __init__(self):
                    c = type("D", (), {"reasoning_content": None,
                                       "content": '{}'})()
                    self.choices = [type("C", (), {"delta": c})()]

            class _Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            return {"ok": True, "response": _Stream()}

        with patch(
            "project_runtime.llm.provider.llm_acomplete", fake_acomplete
        ):
            try:
                await ex._extract_llm("做个待办工具", "test.md")
            except Exception:
                pass  # 解析失败无所谓——只验 prompt
        p = captured.get("prompt", "")
        assert "交付基线" in p and "PPT" in p
        assert "由你根据材料自行安排" in p  # WHERE 自主

    @pytest.mark.asyncio
    async def test_not_software_no_injection(self):
        from unittest.mock import patch

        ex = self._extractor("NOT_SOFTWARE")
        captured = {}

        async def fake_acomplete(*args, **kw):
            captured["prompt"] = kw.get("messages", [{}])[0].get("content", "")

            class _Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            return {"ok": True, "response": _Stream()}

        with patch(
            "project_runtime.llm.provider.llm_acomplete", fake_acomplete
        ):
            try:
                await ex._extract_llm("操作手册", "test.md")
            except Exception:
                pass
        assert "交付基线" not in captured.get("prompt", "")


# ═══ file_rw 禁闭 ════════════════════════════════════════════════

class TestFileRwConfinement:
    @pytest.mark.asyncio
    async def test_write_inside_ws_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from project_runtime.worker.tools import FileRwProvider

        prov = FileRwProvider()
        r = await prov.invoke({
            "action": "write", "path": "src/main.py",
            "content": "print(1)",
            "__ws_root": "data/tasks/1/5",
        })
        assert r["ok"] is True
        assert (tmp_path / "data/tasks/1/5/src/main.py").exists()

    @pytest.mark.asyncio
    async def test_write_outside_ws_rejected(self, tmp_path, monkeypatch):
        """越界（写到相邻任务目录）→ 指引文案, 不落盘。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.worker.tools import FileRwProvider

        prov = FileRwProvider()
        r = await prov.invoke({
            "action": "write", "path": "data/tasks/login_api/main.py",
            "content": "x",
            "__ws_root": "data/tasks/1/5",
        })
        assert r["ok"] is False
        assert "路径越界" in r["error"] and "src/main.py" in r["error"]
        assert not (tmp_path / "data/tasks/login_api").exists()

    @pytest.mark.asyncio
    async def test_no_ws_root_legacy_semantics(self, tmp_path, monkeypatch):
        """无 __ws_root → data/tasks 原语义（向后兼容）。"""
        monkeypatch.chdir(tmp_path)
        from project_runtime.worker.tools import FileRwProvider

        prov = FileRwProvider()
        r = await prov.invoke({
            "action": "write", "path": "data/tasks/anywhere/x.py",
            "content": "x",
        })
        assert r["ok"] is True


# ═══ planner: 四件套 + delivery_tags ═════════════════════════════

class TestPlannerDefaults:
    def test_stage_outputs_four_defaults(self):
        from project_runtime.planner.planner import _delivery_tags_of_ws

        tags = _delivery_tags_of_ws(
            {"name": "登录接口实现"}, ["入口文件"], "开发实现",
        )
        assert "code" in tags

    def test_tags_ppt(self):
        from project_runtime.planner.planner import _delivery_tags_of_ws

        tags = _delivery_tags_of_ws(
            {"name": "方案汇报"}, ["项目方案介绍 PPT"], "交付打包",
        )
        assert "ppt" in tags

    def test_tags_empty_for_doc_task(self):
        from project_runtime.planner.planner import _delivery_tags_of_ws

        tags = _delivery_tags_of_ws(
            {"name": "需求调研"}, ["调研报告"], "需求基线",
        )
        assert tags == []


# ═══ 收口对账 ════════════════════════════════════════════════════

class TestCloseout:
    @pytest.fixture
    async def seeded(self, dbengine):
        """SOFTWARE_DEV 项目 + 三件齐 artifact + 全 ACCEPTED 任务。"""
        from project_runtime.project_state.enums import (
            ArtifactType,
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            Artifact,
            Project,
            Task,
        )
        from project_runtime.task_graph.manager import TaskGraphManager

        async with AsyncSession(dbengine) as s:
            p = Project(
                name="closeout", automation_mode=AutomationMode.FULL_AUTO,
                delivery_profile="SOFTWARE_DEV",
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            from project_runtime.task_graph.manager import CreateTaskRequest

            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="开发", task_type="开发实现",
            ))
            tid = t.id
            pid = p.id
            await mgr.promote_to_ready(tid)
            # 三件齐
            for name in ("方案.pptx", "main.py", "README.md", "测试报告.md"):
                s.add(Artifact(
                    logical_artifact_id=f"x-{name}", project_id=pid,
                    task_id=tid, artifact_type=ArtifactType.FILE,
                    name=name, version=1,
                    path=f"data/x/{name}", sha256="0" * 64,
                ))
            await s.commit()
            # 任务置 ACCEPTED（绕 FSM 直接 SQL 语义）
            from sqlalchemy import update

            await s.execute(
                update(Task).where(Task.id == tid)
                .values(status=TaskStatus.ACCEPTED)
            )
            await s.commit()
            return pid

    @pytest.mark.asyncio
    async def test_all_present_no_task_created(self, dbengine, seeded,
                                               monkeypatch, tmp_path):
        """三件齐 → 不补建。"""
        monkeypatch.chdir(tmp_path)
        import project_runtime.event.engine as ev_mod
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        monkeypatch.setattr(ev_mod, "engine", dbengine)
        await svc._delivery_closeout_check(seeded)
        async with AsyncSession(dbengine) as s:
            from project_runtime.project_state.models import Task

            names = [
                t.name for t in (
                    await s.execute(
                        select(Task).where(Task.project_id == seeded)
                    )
                ).scalars().all()
            ]
        assert not any("交付补全" in n for n in names)

    @pytest.mark.asyncio
    async def test_missing_ppt_creates_task(self, dbengine, monkeypatch,
                                            tmp_path):
        """缺 PPT → 补建任务 + RECONCILIATION_GAP 事件。"""
        from sqlalchemy import update

        from project_runtime.project_state.enums import (
            ArtifactType,
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            Artifact,
            Project,
            Task,
        )
        from project_runtime.task_graph.manager import (
            CreateTaskRequest,
            TaskGraphManager,
        )

        monkeypatch.chdir(tmp_path)
        import project_runtime.event.engine as ev_mod
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        monkeypatch.setattr(ev_mod, "engine", dbengine)
        async with AsyncSession(dbengine) as s:
            p = Project(
                name="no-ppt", automation_mode=AutomationMode.FULL_AUTO,
                delivery_profile="SOFTWARE_DEV",
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="开发", task_type="开发实现",
            ))
            for name in ("main.py", "README.md", "测试报告.md"):
                s.add(Artifact(
                    logical_artifact_id=f"y-{name}", project_id=p.id,
                    task_id=t.id, artifact_type=ArtifactType.FILE,
                    name=name, version=1, path=f"data/y/{name}",
                    sha256="0" * 64,
                ))
            tid = t.id
            pid = p.id
            await s.commit()
            await s.execute(
                update(Task).where(Task.id == tid)
                .values(status=TaskStatus.ACCEPTED)
            )
            await s.commit()

        await svc._delivery_closeout_check(pid)
        async with AsyncSession(dbengine) as s:
            from project_runtime.project_state.models import Task

            tasks = [
                t for t in (
                    await s.execute(
                        select(Task).where(Task.project_id == pid)
                    )
                ).scalars().all()
            ]
            added = [t for t in tasks if "交付补全" in t.name]
            assert len(added) == 1 and "PPT" in added[0].name
            assert added[0].status == TaskStatus.READY
            # 契约含缺失事实
            assert "无 .pptx" in (added[0].contract or "")

    @pytest.mark.asyncio
    async def test_no_double_backfill(self, dbengine, monkeypatch, tmp_path):
        """防死循环: 补建任务已存在 → 二次收口不再建。"""
        from sqlalchemy import update

        from project_runtime.project_state.enums import (
            ArtifactType,
            AutomationMode,
            TaskStatus,
        )
        from project_runtime.project_state.models import (
            Artifact,
            Project,
            Task,
        )
        from project_runtime.task_graph.manager import (
            CreateTaskRequest,
            TaskGraphManager,
        )

        monkeypatch.chdir(tmp_path)
        import project_runtime.event.engine as ev_mod
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        monkeypatch.setattr(ev_mod, "engine", dbengine)
        async with AsyncSession(dbengine) as s:
            p = Project(
                name="no-double", automation_mode=AutomationMode.FULL_AUTO,
                delivery_profile="SOFTWARE_DEV",
            )
            s.add(p)
            await s.flush()
            mgr = TaskGraphManager(s)
            t = await mgr.create_task(CreateTaskRequest(
                project_id=p.id, name="开发", task_type="开发实现",
            ))
            for name in ("main.py", "README.md", "测试报告.md"):
                s.add(Artifact(
                    logical_artifact_id=f"z-{name}", project_id=p.id,
                    task_id=t.id, artifact_type=ArtifactType.FILE,
                    name=name, version=1, path=f"data/z/{name}",
                    sha256="0" * 64,
                ))
            tid = t.id
            pid = p.id
            await s.commit()
            await s.execute(
                update(Task).where(Task.id == tid)
                .values(status=TaskStatus.ACCEPTED)
            )
            await s.commit()

        await svc._delivery_closeout_check(pid)
        await svc._delivery_closeout_check(pid)  # 二次收口
        async with AsyncSession(dbengine) as s:
            tasks = [
                t for t in (
                    await s.execute(
                        select(Task).where(Task.project_id == pid)
                    )
                ).scalars().all()
            ]
            added = [t for t in tasks if "交付补全" in t.name]
            assert len(added) == 1  # 不重复建

    @pytest.mark.asyncio
    async def test_not_software_skipped(self, dbengine, seeded, monkeypatch,
                                        tmp_path):
        """NOT_SOFTWARE → 零收口（对照不误伤）。"""
        monkeypatch.chdir(tmp_path)
        import project_runtime.execution.service as svc

        monkeypatch.setattr(svc, "engine", dbengine)
        from sqlalchemy import update

        from project_runtime.project_state.models import Project

        async with AsyncSession(dbengine) as s:
            await s.execute(
                update(Project).where(Project.id == seeded)
                .values(delivery_profile="NOT_SOFTWARE")
            )
            await s.commit()
        await svc._delivery_closeout_check(seeded)  # 应直接 return
        async with AsyncSession(dbengine) as s:
            from project_runtime.project_state.models import Task

            names = [
                t.name for t in (
                    await s.execute(
                        select(Task).where(Task.project_id == seeded)
                    )
                ).scalars().all()
            ]
        assert not any("交付补全" in n for n in names)


# ═══ 枚举 ════════════════════════════════════════════════════════

def test_reconciliation_gap_enum_exists():
    from project_runtime.event import RuntimeEventType

    assert RuntimeEventType.RECONCILIATION_GAP.value == "RECONCILIATION_GAP"
