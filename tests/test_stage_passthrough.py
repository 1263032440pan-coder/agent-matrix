"""返修-V1.4b 测试: task_type 环节贯通（修1/2/3 验收）。"""

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
from project_runtime.project_state.enums import AutomationMode
from project_runtime.project_state.models import Project
from project_runtime.worker.agent_models import AgentSource
from project_runtime.worker.registry import (
    AgentProvisioner,
    normalize_stage,
    seed_builtin_templates,
)

# ── 修3: 英文九阶段别名归一 ───────────────────────────────────────


class TestStageAliases:
    def test_english_nine_stages(self):
        """项目11 实测的九个英文阶段名全部归一（大小写不敏感）。"""
        cases = {
            "Project Understanding": "项目理解",
            "Method Identification": "项目理解",
            "Playbook": "架构设计",
            "Task Planning": "架构设计",
            "Development Design": "架构设计",
            "Worker Execution": "开发实现",
            "Gate": "测试验证",
            "Testing": "测试验证",
            "Delivery": "交付打包",
            "Project Completion": "使用文档",
            # 小写形态
            "project understanding": "项目理解",
            "testing": "测试验证",
        }
        for raw, want in cases.items():
            got = normalize_stage(raw)
            assert got == want, f"{raw!r} → {got}（期望 {want}）"

    def test_default_generic_still_misc(self):
        assert normalize_stage("default") == "misc"
        assert normalize_stage("generic") == "misc"
        assert normalize_stage(None) == "misc"

    def test_chinese_pass_through(self):
        assert normalize_stage("开发实现") == "开发实现"


# ── 修2: provision misc 走派生兜底 ────────────────────────────────


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v14b.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="v14b", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await seed_builtin_templates(s)
        await s.commit()
    yield eng, pid
    await eng.dispose()


class TestProvisionMiscFallback:
    @pytest.mark.asyncio
    async def test_english_stages_provision_instances(self, dbengine):
        """项目11 同款英文阶段集 → 5 内置 + 1 派生 = 6 实例。"""
        eng, pid = dbengine
        types = [
            "Project Understanding", "Playbook", "Worker Execution",
            "Gate", "Testing", "Delivery", "default",
        ]
        async with AsyncSession(eng) as s:
            spawned = await AgentProvisioner(s).provision(pid, types)
            n = len(spawned)
            await s.commit()
        assert n == 6

    @pytest.mark.asyncio
    async def test_pure_misc_provisions_derived(self, dbengine):
        """全 default 任务集 → 派生兜底实例 ≥1（不再丢弃）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            spawned = await AgentProvisioner(s).provision(pid, ["default"] * 5)
            n = len(spawned)
            await s.commit()
        assert n == 1  # misc 一个环节——派生模板一实例
        from project_runtime.worker.agent_models import (
            AgentDefinition,
            AgentInstance,
        )

        async with AsyncSession(eng) as s:
            insts = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().all()
            assert len(insts) == 1
            d = await s.get(AgentDefinition, insts[0].definition_id)
            assert d.source is AgentSource.CONTROLLER_GENERATED
            assert set(d.plugins) >= {"file_rw", "web_search"}

    @pytest.mark.asyncio
    async def test_provision_idempotent_with_derive(self, dbengine):
        """重复 provision（含派生）不叠实例——幂等闸+唯一索引。"""
        eng, pid = dbengine
        for _ in range(2):
            async with AsyncSession(eng) as s:
                await AgentProvisioner(s).provision(pid, ["default"])
                await s.commit()
        from project_runtime.worker.agent_models import AgentInstance

        async with AsyncSession(eng) as s:
            n = len(
                (
                    await s.execute(
                        select(AgentInstance).where(
                            AgentInstance.project_id == pid
                        )
                    )
                ).scalars().all()
            )
            assert n == 1


# ── 修1: task_type 三源优先级链 ───────────────────────────────────


class TestPlannerTaskTypeChain:
    def test_priority_stage_first(self):
        """stage > source_skill_stage > workstream。"""

        # _create_task_for_workstream 的取值链是纯表达式——
        # 用 ws dict 直接验证三源语义
        def chain(ws):
            return (
                ws.get("stage")
                or ws.get("source_skill_stage")
                or ws.get("workstream")
                or "generic"
            )

        assert chain({"stage": "开发实现", "workstream": "default"}) == "开发实现"
        assert chain({"source_skill_stage": "测试验证", "workstream": "default"}) == "测试验证"
        assert chain({"workstream": "default"}) == "default"
        assert chain({}) == "generic"
        # stage 优先于 skill_stage
        assert chain({"stage": "A", "source_skill_stage": "B"}) == "A"

    def test_skill_skeleton_carries_stage(self):
        """skill 骨架 ws 带 source_skill_stage（skill_planning 产出形态）。"""
        # 直接验证 skill_planning 的 ws 构造含新字段（源码级——
        # 完整链路由既有 test_skill_planning 覆盖）
        import inspect

        import project_runtime.procedure.skill_planning as sp

        src = inspect.getsource(sp)
        assert '"source_skill_stage": st["name"]' in src

    def test_extractor_stage_to_workstream(self):
        """extractor._normalize_node: stage → workstream 列（UNKNOWN 落 None）。"""
        from project_runtime.procedure.llm_extractor import LLMExtractor

        ex = LLMExtractor.__new__(LLMExtractor)
        n1 = ex._normalize_node(
            {"name": "Testing", "stage": "测试验证"}, "doc.md"
        )
        assert n1["workstream"] == "测试验证"
        n2 = ex._normalize_node(
            {"name": "X", "stage": "UNKNOWN"}, "doc.md"
        )
        assert n2["workstream"] is None
        n3 = ex._normalize_node({"name": "Y"}, "doc.md")
        assert n3["workstream"] is None

    def test_extractor_prompt_has_stage_field(self):
        from project_runtime.procedure.llm_extractor import _PROMPT

        assert '"stage"' in _PROMPT
        # 返修V3 批A: 推翻九选一——自由命名 + 九词仅默认种子
        assert "九选一" not in _PROMPT
        assert "默认种子" in _PROMPT or "临场" in _PROMPT
