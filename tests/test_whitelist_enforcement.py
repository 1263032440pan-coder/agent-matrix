"""返修-20号-V1.4 Phase 1 测试: 白名单执法补全（全离线）.

覆盖（返修单 §1.5）:
  [x] 交集正/反: 白名单内工具可见、外不出现在工具面且分发拒绝
  [x] None vs [] 两态语义（None=全量 / []=零）
  [x] 无 Agent 绑定路径工具面不变（13 个）
  [x] SYS-147: 旧白名单 file_rw 别名展开 / 调用旧名 TOOL_RENAMED
  [x] web_search 按 Agent 白名单门控
  [x] skill 白名单: restrict_to 过滤候选 / None 全局
  [x] 修订后五模板含三能力基线、skills=None、lsp 摘除
  [x] seed upsert: BUILT_IN 更新 / USER 不覆盖 / 幂等
"""

import pytest
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
from project_runtime.project_state.enums import AutomationMode
from project_runtime.project_state.models import Project
from project_runtime.worker.agent_models import AgentSource
from project_runtime.worker.registry import (
    BUILT_IN_TEMPLATES,
    seed_builtin_templates,
)


@pytest.fixture
def full_registry():
    from project_runtime.extension.shared import shared_registry
    from project_runtime.research.providers import register_search_providers
    from project_runtime.worker.coding_tools import register_batch3_tools
    from project_runtime.worker.tools import register_worker_tools

    reg = shared_registry()
    register_search_providers(reg)
    register_worker_tools(reg)
    register_batch3_tools(reg)
    return reg


def _worker():
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    return LLMWorkerAdapter.__new__(LLMWorkerAdapter)


_FILE_TOOLS = (
    "tool:read_file", "tool:list_dir", "tool:write_file", "tool:append_file",
)
_SKILL_TOOLS = ("tool:skills_list", "tool:skill_view")


def _names(tools) -> set[str]:
    return {t["function"]["name"] for t in (tools or [])}


# ── 工具循环交集 ───────────────────────────────────────────────────


class TestToolLoopWhitelist:
    def test_coder_view_intersected(self, full_registry):
        """coder 白名单 → 5 工具（白名单外 office_pptx 等不可见）。"""
        w = _worker()
        tools = w._search_tools(whitelist=[
            "read_file", "list_dir", "write_file", "append_file",
            "web_search", "shell", "code_search", "git",
        ])
        assert _names(tools) == {
            "web_search", *_FILE_TOOLS, *_SKILL_TOOLS, "tool:shell",
            "tool:code_search", "tool:git",
        }

    def test_analyst_view_intersected(self, full_registry):
        w = _worker()
        tools = w._search_tools(whitelist=[
            "read_file", "list_dir", "write_file", "append_file",
            "web_search", "webfetch", "office_pptx",
        ])
        assert _names(tools) == {
            "web_search", *_FILE_TOOLS, *_SKILL_TOOLS,
            "tool:webfetch", "tool:office_pptx",
        }
        # shell 对 analyst 不可见（环节边界——护栏生效）
        assert "tool:shell" not in _names(tools)

    def test_none_means_all(self, full_registry):
        """None = 不限（含 Hermes office 工具族）。"""
        w = _worker()
        names = _names(w._search_tools(whitelist=None))
        assert "tool:office_pptx" in names
        assert "tool:image_raster" in names
        assert len(names) >= 18

    def test_empty_list_fail_closed(self, full_registry):
        """[] = 零工具（fail-closed，Hermes #82010 约定）。"""
        w = _worker()
        assert w._search_tools(whitelist=[]) == []

    def test_web_search_gated(self, full_registry):
        """白名单不含 web_search → 搜索工具不可见（护栏优先）。"""
        w = _worker()
        tools = w._search_tools(whitelist=["write_file"])
        assert "web_search" not in _names(tools)
        # Skill 目录工具对非空白名单恒放行（安装即可用）
        assert _names(tools) == {"tool:write_file", *_SKILL_TOOLS}

    def test_legacy_file_rw_whitelist_expands(self, full_registry):
        """SYS-147: 存库旧白名单 file_rw → 三个新文件工具，旧名不暴露。"""
        w = _worker()
        names = _names(w._search_tools(whitelist=["file_rw"]))
        assert names == set(_FILE_TOOLS) | set(_SKILL_TOOLS)

    @pytest.mark.asyncio
    async def test_legacy_file_rw_whitelist_dispatches(
        self, full_registry, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/w1").mkdir(parents=True, exist_ok=True)
        w = _worker()
        w._workspace_root = None
        result = await w._run_tool_plugin(
            "tool:write_file",
            {"path": "data/tasks/w1/a.md", "content": "x"},
            whitelist=["file_rw"],
        )
        assert result is not None and result["ok"] is True

    @pytest.mark.asyncio
    async def test_calling_file_rw_returns_renamed(self, full_registry):
        w = _worker()
        result = await w._run_tool_plugin(
            "tool:file_rw", {"action": "write", "path": "a.md"},
            whitelist=["file_rw"],
        )
        assert result is not None and result["ok"] is False
        assert result["error_code"] == "TOOL_RENAMED"
        assert "write_file" in result["hint"]

    @pytest.mark.asyncio
    async def test_dispatch_rejects_outside_whitelist(self, full_registry):
        """纵深防御: 白名单外工具名分发直接拒（含降级文案）。"""
        w = _worker()
        result = await w._run_tool_plugin(
            "tool:git", {}, whitelist=["file_rw"]
        )
        assert result is not None and result["ok"] is False
        assert "白名单" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_allows_inside_whitelist(
        self, full_registry, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/w1").mkdir(parents=True, exist_ok=True)
        w = _worker()
        result = await w._run_tool_plugin(
            "tool:todo",
            {"action": "list", "workspace": "data/tasks/w1"},
            whitelist=["todo"],
        )
        assert result is not None and result["ok"] is True


# ── skill 白名单 ───────────────────────────────────────────────────


class TestSkillWhitelist:
    @pytest.mark.asyncio
    async def test_restrict_to_filters_candidates(self, tmp_path):
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sk.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from project_runtime.skills.models import SkillDefinition
        from project_runtime.skills.selector import SkillSelector

        async with AsyncSession(eng) as s:
            s.add(SkillDefinition(
                skill_id="web-dev", name="W", version="1",
                skill_type="TASK_EXECUTION",
                content_hash="h1", when_to_use="开发网页",
            ))
            s.add(SkillDefinition(
                skill_id="doc-write", name="D", version="1",
                skill_type="TASK_EXECUTION",
                content_hash="h2", when_to_use="写文档",
            ))
            await s.commit()
            sel = SkillSelector(s)
            pool = await sel.candidates(None)
            assert len(pool) == 2
            # 白名单只留 web-dev
            sel2 = SkillSelector(s).restrict_to(["web-dev"])
            pool2 = await sel2.candidates(None)
            assert [r.skill_id for r in pool2] == ["web-dev"]
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_none_keeps_global_pool(self, tmp_path):
        """restrict_to(None) = 不限——全局候选池（默认语义）。"""
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sk2.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from project_runtime.skills.models import SkillDefinition
        from project_runtime.skills.selector import SkillSelector

        async with AsyncSession(eng) as s:
            s.add(SkillDefinition(
                skill_id="a", name="A", version="1",
                skill_type="TASK_EXECUTION",
                content_hash="h1", when_to_use="x",
            ))
            s.add(SkillDefinition(
                skill_id="b", name="B", version="1",
                skill_type="TASK_EXECUTION",
                content_hash="h2", when_to_use="y",
            ))
            await s.commit()
            sel = SkillSelector(s).restrict_to(None)
            assert len(await sel.candidates(None)) == 2
        await eng.dispose()


# ── 模板修订 + seed upsert ─────────────────────────────────────────


class TestTemplateRevisionAndSeed:
    def test_five_templates_have_baseline(self):
        """修订后五模板必含三能力基线（文件工具 + web_search + Skill 目录）。"""
        for tpl in BUILT_IN_TEMPLATES:
            plugins = tpl["plugins"]
            assert "file_rw" not in plugins, tpl["agent_id"]
            for name in ("read_file", "list_dir", "write_file", "append_file"):
                assert name in plugins, (tpl["agent_id"], name)
            assert "web_search" in plugins, tpl["agent_id"]
            for name in ("skills_list", "skill_view"):
                assert name in plugins, (tpl["agent_id"], name)

    def test_lsp_removed(self):
        for tpl in BUILT_IN_TEMPLATES:
            assert "lsp" not in tpl["plugins"], tpl["agent_id"]

    def test_skills_none_not_empty(self):
        """skills=None（不限）而非 []（fail-closed 零 skill）。"""
        for tpl in BUILT_IN_TEMPLATES:
            assert tpl["skills"] is None, tpl["agent_id"]

    @pytest.mark.asyncio
    async def test_seed_upsert_builtin(self, tmp_path):
        """BUILT_IN 行随代码 upsert（白名单修订能到达存量库）。"""
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sd.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from project_runtime.worker.agent_models import AgentDefinition

        async with AsyncSession(eng) as s:
            n = await seed_builtin_templates(s)
            await s.commit()
            assert n == 5
        # 手工把 coder 白名单改旧（模拟开发库存量旧行）
        async with AsyncSession(eng) as s:
            row = (await s.execute(
                __import__("sqlalchemy").select(AgentDefinition).where(
                    AgentDefinition.agent_id == "coder")
            )).scalar_one()
            row.plugins = ["file_rw", "shell"]  # 旧值: 无 web_search
            await s.commit()
        # 再 seed → upsert 拉回出厂配置
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            await s.commit()
        async with AsyncSession(eng) as s:
            row = (await s.execute(
                __import__("sqlalchemy").select(AgentDefinition).where(
                    AgentDefinition.agent_id == "coder")
            )).scalar_one()
            assert "web_search" in (row.plugins or [])
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_seed_never_overwrites_user(self, tmp_path):
        """USER 来源的行 seed 永不覆盖。"""
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sd2.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from project_runtime.worker.agent_models import AgentDefinition

        async with AsyncSession(eng) as s:
            s.add(AgentDefinition(
                agent_id="coder", name="我的定制", source=AgentSource.USER,
                plugins=["file_rw"],
            ))
            await s.commit()
        async with AsyncSession(eng) as s:
            n = await seed_builtin_templates(s)
            await s.commit()
            assert n == 4  # coder 已占——只插其余四个
        async with AsyncSession(eng) as s:
            row = (await s.execute(
                __import__("sqlalchemy").select(AgentDefinition).where(
                    AgentDefinition.agent_id == "coder")
            )).scalar_one()
            assert row.source is AgentSource.USER
            assert row.plugins == ["file_rw"]  # 未被覆盖
        await eng.dispose()

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, tmp_path):
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sd3.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from sqlalchemy import func, select

        from project_runtime.worker.agent_models import AgentDefinition

        for _ in range(2):
            async with AsyncSession(eng) as s:
                await seed_builtin_templates(s)
                await s.commit()
        async with AsyncSession(eng) as s:
            n = (await s.execute(
                select(func.count()).select_from(AgentDefinition)
            )).scalar_one()
            assert n == 5
        await eng.dispose()


# ── bind 快照透传 ──────────────────────────────────────────────────


class TestBindingSnapshot:
    @pytest.mark.asyncio
    async def test_bind_carries_whitelist(self, tmp_path):
        """bind_agent 快照 definition 白名单进 AgentBinding。"""
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bd.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with AsyncSession(eng) as s:
            p = Project(name="bd", automation_mode=AutomationMode.FULL_AUTO)
            s.add(p)
            await s.flush()
            pid = p.id
            await s.commit()
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            from project_runtime.worker.registry import AgentProvisioner

            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()
        from project_runtime.worker.lifecycle import bind_agent

        async with AsyncSession(eng) as s:
            binding = await bind_agent(
                s, pid, task_id=9, task_type="开发实现"
            )
            await s.commit()
        assert binding is not None
        # coder 模板白名单（含修订后的 web_search）
        assert binding.tool_whitelist is not None
        assert "web_search" in binding.tool_whitelist
        assert binding.skill_whitelist is None  # None=不限
        await eng.dispose()
