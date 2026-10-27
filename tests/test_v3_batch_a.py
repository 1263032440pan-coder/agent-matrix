"""返修单 V3 批A: 流程 AI 合成 + 语义派活 + selector 中性引导。

SYS-086/087/088 验收锚点（离线 stub，真 LLM 不进 CI）。
"""

from __future__ import annotations

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
from project_runtime.worker.agent_models import AgentDefinition
from project_runtime.worker.registry import (
    AgentProvisioner,
    WorkerRegistry,
    seed_builtin_templates,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3a.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="v3a", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await seed_builtin_templates(s)
        await s.commit()
    yield eng, pid
    await eng.dispose()


class TestA5SelectorNeutrality:
    def test_skill_planning_prompt_neutral(self):
        """SYS-088: 引导词不再偏置『软件项目交付方法』。"""
        import inspect

        import project_runtime.procedure.skill_planning as sp

        src = inspect.getsource(sp.read_skill_stages)
        assert "需要软件项目交付方法类 skill" not in src
        assert "PROJECT_METHOD" in src


class TestA1FreeStageSynthesis:
    def test_extractor_prompt_not_nine_choice(self):
        """SYS-086: stage 自由命名；九词仅为默认种子参考。"""
        from project_runtime.procedure.llm_extractor import _PROMPT

        assert "九选一" not in _PROMPT
        assert "默认种子" in _PROMPT or "参考" in _PROMPT
        # 九词仍可作为无参考时的种子出现
        assert "项目理解" in _PROMPT
        assert "自由" in _PROMPT or "临场" in _PROMPT or "材料" in _PROMPT

    def test_free_stage_passthrough_to_workstream(self):
        """自由阶段名（如任务图构建）原样进 workstream，不逼进九格。"""
        from project_runtime.llm import LLMSettings
        from project_runtime.procedure.llm_extractor import LLMExtractor

        ex = LLMExtractor(LLMSettings())
        n = ex._normalize_node(
            {"name": "构建任务图", "stage": "任务图构建"},
            "doc.md",
        )
        assert n["workstream"] == "任务图构建"


class TestA2SemanticRoute:
    @pytest.mark.asyncio
    async def test_semantic_route_picks_designer_for_task_graph(
        self, dbengine, monkeypatch,
    ):
        """SYS-087: 『任务图构建』经语义派活归入设计智能体，不落 misc。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await AgentProvisioner(s).provision(
                pid, ["架构设计", "开发实现", "项目理解"],
            )
            await s.commit()

        async def _fake_pick(self, pool, task_type, task_content):
            # 模拟 LLM：任务图构建 → designer
            for agent_id, _inst, _d in pool:
                if agent_id == "designer":
                    return agent_id
            return None

        monkeypatch.setattr(
            WorkerRegistry, "_semantic_pick_agent", _fake_pick,
        )

        async with AsyncSession(eng) as s:
            reg = WorkerRegistry(s)
            inst = await reg.route(
                pid,
                "任务图构建",
                task_content="根据 skill 阶段『任务图构建』拆解任务依赖图",
            )
            assert inst is not None
            d = await s.get(AgentDefinition, inst.definition_id)
            assert d.agent_id == "designer"
            # 权限天花板：仍是模板 plugins，AI 挑不出新工具
            assert "shell" not in (d.plugins or [])
            assert "arch_diagram" in (d.plugins or [])

    @pytest.mark.asyncio
    async def test_semantic_ambiguous_falls_to_seed(
        self, dbengine, monkeypatch,
    ):
        """SYS-088/A4: AI 判定 AMBIGUOUS → 退九阶段种子亲和（不硬选）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()

        async def _amb(_self, _pool, _tt, _tc):
            return "AMBIGUOUS"

        monkeypatch.setattr(
            WorkerRegistry, "_semantic_pick_agent", _amb,
        )

        async with AsyncSession(eng) as s:
            reg = WorkerRegistry(s)
            # 无语义命中时，『开发实现』仍可走种子亲和
            inst = await reg.route(pid, "开发实现", task_content="写代码")
            assert inst is not None
            d = await s.get(AgentDefinition, inst.definition_id)
            assert d.agent_id == "coder"

    @pytest.mark.asyncio
    async def test_llm_down_seed_route_still_works(self, dbengine):
        """SYS-086 验收: LLM 不可用 → 退九阶段种子路由不阻断。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await AgentProvisioner(s).provision(pid, ["测试验证"])
            await s.commit()
        async with AsyncSession(eng) as s:
            reg = WorkerRegistry(s)
            # 无 mock、无 LLM 配置 → _semantic_pick 返回 None → 种子亲和
            inst = await reg.route(pid, "测试验证")
            assert inst is not None
            d = await s.get(AgentDefinition, inst.definition_id)
            assert d.agent_id == "tester"

    @pytest.mark.asyncio
    async def test_semantic_never_invents_agent(
        self, dbengine, monkeypatch,
    ):
        """只能挑池内注册的 agent_id。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await AgentProvisioner(s).provision(pid, ["开发实现"])
            await s.commit()

        async def _hallucinate(_self, _pool, _tt, _tc):
            return "nonexistent-agent-xyz"

        monkeypatch.setattr(
            WorkerRegistry, "_semantic_pick_agent", _hallucinate,
        )

        async with AsyncSession(eng) as s:
            reg = WorkerRegistry(s)
            # 幻觉 id 被拒 → 退种子；『随便什么』→ misc，池内无 misc 亲和
            # 仅 coder 在池 → 自由名无种子命中则 None（不编造）
            inst = await reg.route(
                pid, "外星阶段", task_content="无关",
            )
            # 种子亲和 miss 且幻觉无效 → None（宁可不派，不编造）
            assert inst is None
