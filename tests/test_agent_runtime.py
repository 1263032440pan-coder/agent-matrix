"""20号规范批次 1: Agent 实体/FSM/路由/压缩 测试（全离线）.

覆盖:
  [x] 六态 FSM 全转移正/反（非法转移拒绝 + actor 防护）
  [x] DEGRADED 自愈（冷却期满→IDLE）/ record_failure 阈值
  [x] 归一化三步（精确/别名/misc；default+generic 双词都落 misc）
  [x] 路由: 环节匹配/别名命中/插件需求过滤/无匹配 None
  [x] provision: seed 五模板/环节归组/幂等复用
  [x] 阈值模型适配（deepseek 112K/小窗口/默认兜底）
  [x] SessionStore 归档幂等/JSON 往返
  [x] 压缩: Prune 剥工具结果+保护带/组装五段（system 头+卡片+尾部原文）
  [x] 关键串保真（路径/artifact#/sha/URL 提取）
  [x] 大历史 fixture（30 任务 210K→组装 14.5K 视图）
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
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentSource,
    AgentStatus,
)
from project_runtime.worker.context import (
    ContextAssembler,
    ContextCompressor,
    SessionStore,
    TaskRecord,
    compression_threshold,
    extract_critical_strings,
)
from project_runtime.worker.registry import (
    AgentFSM,
    AgentProvisioner,
    WorkerRegistry,
    normalize_stage,
    seed_builtin_templates,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ag.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="ag", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


async def _mk_instance(session, pid, status=AgentStatus.CREATED) -> AgentInstance:
    d = AgentDefinition(
        agent_id=f"d{pid}_{status.value}", name="D",
        source=AgentSource.BUILT_IN,
    )
    session.add(d)
    await session.flush()
    inst = AgentInstance(
        definition_id=d.id, project_id=pid, status=status
    )
    session.add(inst)
    await session.flush()
    return inst


# ── FSM ────────────────────────────────────────────────────────────


class TestAgentFSM:
    @pytest.mark.asyncio
    async def test_legal_transitions(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            fsm = AgentFSM(s)
            inst = await _mk_instance(s, pid)
            await fsm.transition(inst, AgentStatus.IDLE, actor="SYSTEM")
            await fsm.transition(inst, AgentStatus.BUSY, actor="SYSTEM")
            await fsm.transition(inst, AgentStatus.IDLE, actor="SYSTEM")
            final = inst.status  # commit 前快照（ORM expire 老坑）
            await s.commit()
            assert final is AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_illegal_edge_rejected(self, dbengine):
        from project_runtime.worker.registry import IllegalAgentTransitionError

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            fsm = AgentFSM(s)
            inst = await _mk_instance(s, pid, AgentStatus.CREATED)
            # CREATED→BUSY 不存在边
            with pytest.raises(IllegalAgentTransitionError):
                await fsm.transition(inst, AgentStatus.BUSY, actor="SYSTEM")

    @pytest.mark.asyncio
    async def test_actor_guard_paused(self, dbengine):
        """PAUSED 仅 Controller/USER——Agent 自己（SYSTEM）不能暂停。"""
        from project_runtime.worker.registry import IllegalAgentTransitionError

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            fsm = AgentFSM(s)
            inst = await _mk_instance(s, pid, AgentStatus.IDLE)
            with pytest.raises(IllegalAgentTransitionError):
                await fsm.transition(inst, AgentStatus.PAUSED, actor="SYSTEM")
            # Controller 可以
            await fsm.transition(inst, AgentStatus.PAUSED, actor="CONTROLLER")
            assert inst.status is AgentStatus.PAUSED

    @pytest.mark.asyncio
    async def test_degraded_selfheal(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            fsm = AgentFSM(s)
            inst = await _mk_instance(s, pid, AgentStatus.BUSY)
            # 3 连败 → DEGRADED + 冷却
            for _ in range(3):
                await fsm.record_failure(inst)
            assert inst.status is AgentStatus.DEGRADED
            assert inst.degraded_until is not None
            # 冷却未满不自愈
            assert await fsm.try_recover(inst) is False
            # 人工把冷却拨到过去 → 自愈
            from datetime import datetime, timezone

            inst.degraded_until = datetime(2020, 1, 1, tzinfo=timezone.utc)
            assert await fsm.try_recover(inst) is True
            assert inst.status is AgentStatus.IDLE

    @pytest.mark.asyncio
    async def test_terminated_is_terminal(self, dbengine):
        from project_runtime.worker.registry import IllegalAgentTransitionError

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            fsm = AgentFSM(s)
            inst = await _mk_instance(s, pid, AgentStatus.TERMINATED)
            # TERMINATED 无出边
            with pytest.raises(IllegalAgentTransitionError):
                await fsm.transition(inst, AgentStatus.IDLE, actor="USER")


# ── 归一化与路由 ───────────────────────────────────────────────────


class TestStageAndRouting:
    def test_normalize_exact(self):
        assert normalize_stage("开发实现") == "开发实现"
        assert normalize_stage("测试验证") == "测试验证"

    def test_normalize_alias(self):
        assert normalize_stage("编码") == "开发实现"
        assert normalize_stage("需求分析") == "需求基线"

    def test_normalize_fallback_words(self):
        """兜底词统一（§6.1 第 5 条）: default/generic 双词都归 misc。"""
        assert normalize_stage("default") == "misc"
        assert normalize_stage("generic") == "misc"

    def test_normalize_unknown(self):
        assert normalize_stage("随便什么") == "misc"
        assert normalize_stage(None) == "misc"
        assert normalize_stage("") == "misc"

    @pytest.mark.asyncio
    async def test_route_and_plugin_filter(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            prov = AgentProvisioner(s)
            await prov.provision(pid, ["开发实现", "测试验证"])
            await s.commit()
        async with AsyncSession(eng) as s:
            reg = WorkerRegistry(s)
            coder = await reg.route(pid, "开发实现")
            assert coder is not None
            d = await s.get(AgentDefinition, coder.definition_id)
            assert d.agent_id == "coder"
            # 别名路由到同实例
            alias = await reg.route(pid, "编码")
            assert alias is not None and alias.id == coder.id
            # 插件需求过滤: 要求 tester 没有的插件 → 不中
            miss = await reg.route(
                pid, "测试验证", required_plugins=["git"]
            )
            assert miss is None
            # tester 有的插件 → 中
            hit = await reg.route(
                pid, "测试验证", required_plugins=["test_runner"]
            )
            assert hit is not None

    @pytest.mark.asyncio
    async def test_provision_idempotent(self, dbengine):
        """重复 provision 幂等（活跃实例复用不重建）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await seed_builtin_templates(s)
            p1 = await AgentProvisioner(s).provision(
                pid, ["开发实现", "测试验证"]
            )
            await s.commit()
        async with AsyncSession(eng) as s:
            p2 = await AgentProvisioner(s).provision(
                pid, ["开发实现", "测试验证"]
            )
            await s.commit()
        assert len(p1) == 2
        assert len(p2) == 0, "二次 provision 应零新建"

    @pytest.mark.asyncio
    async def test_seed_builtin_idempotent(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            n1 = await seed_builtin_templates(s)
            n2 = await seed_builtin_templates(s)
            await s.commit()
        assert n1 == 5
        assert n2 == 0


# ── 阈值与压缩 ─────────────────────────────────────────────────────


class TestCompression:
    def test_threshold_deepseek(self):
        """opencode 原式: 128K − min(20K, 16K) = 112K。"""
        assert compression_threshold("qwen/deepseek-v4-pro") == 112000

    def test_threshold_user_override(self, monkeypatch):
        """用户覆盖优先（§7.5+ 第①层）: model_overrides 指定 32K 窗口
        → 阈值 32K−20K=12K（小窗口自动激进）。"""
        from project_runtime import llm as llm_mod

        monkeypatch.setattr(
            llm_mod, "load_llm_settings",
            lambda: type("C", (), {
                "base_url": None, "api_key": None, "model": None,
                "model_overrides": {
                    "my-32k-model": {"context": 32768, "output": 4096}
                },
            })(),
        )
        assert compression_threshold("my-32k-model") == 32768 - 4096  # min(20K, output=4096)

    def test_threshold_unknown_default(self):
        """未知模型 → 保守默认 128K/16K → 112K。"""
        assert compression_threshold("totally-unknown") == 112000

    def test_threshold_builtin_models(self):
        # deepseek-chat: 128K − min(20K, 8192) = 119808
        assert compression_threshold("deepseek-chat") == 119808
        # gpt-4o: 128K − min(20K, 16384) = 111616
        assert compression_threshold("gpt-4o") == 111616

    def test_session_store_roundtrip(self):
        store = SessionStore()
        store.archive_task(1, "任务A", [{"role": "user", "content": "x"}])
        store.archive_task(1, "任务A-v2", [{"role": "user", "content": "y"}])
        raw = store.to_json()
        loaded = SessionStore.from_json(raw)
        assert len(loaded.tasks) == 1  # 幂等覆盖
        assert loaded.tasks[0].task_name == "任务A-v2"
        assert SessionStore.from_json(None).tasks == []
        assert SessionStore.from_json("bad json").tasks == []

    def test_critical_strings(self):
        text = (
            "产物 artifact#91 与 data/tasks/9/68/需求基线_output.v1.md "
            "sha:3f2abc91 见 https://example.com/doc"
        )
        out = extract_critical_strings(text)
        assert any("artifact#91" in x for x in out)
        assert any("data/tasks/9/68" in x for x in out)
        assert any("3f2abc91" in x for x in out)
        assert any("https://example.com" in x for x in out)

    @pytest.mark.asyncio
    async def test_prune_and_protect_band(self):
        """Prune 剥保护带外工具结果；保护带内完整。"""
        store = SessionStore()
        for i in range(30):
            store.archive_task(100 + i, f"任务{i}", [
                {"role": "user", "content": f"契约{i}" + "x" * 20000},
                {"role": "tool", "content": f"工具{i}" + "z" * 8000},
            ])
        c = ContextCompressor("qwen/deepseek-v4-pro")
        assert c.should_compress(store) is True
        stats = await c.compress(store, current_task_ids=[129])
        assert stats["before"] == 210040
        assert stats["after"] < stats["before"]
        assert stats["pruned_tasks"] > 0
        # 保护带内（尾部）工具结果未清
        intact = sum(
            1 for t in store.tasks if t.task_id >= 120
            for m in t.messages
            if m["role"] == "tool" and "清理" not in m["content"]
        )
        assert intact > 0

    @pytest.mark.asyncio
    async def test_assemble_five_segments(self):
        """组装五段: system 头 + 卡片/占位区 + 尾部原文 + 当前段。"""
        store = SessionStore()
        for i in range(15):
            store.archive_task(100 + i, f"任务{i}", [
                {"role": "user", "content": f"契约{i}" + "x" * 20000},
                {"role": "tool", "content": f"工具{i}" + "z" * 8000},
            ])
        asm = ContextAssembler("qwen/deepseek-v4-pro")
        messages, stats = await asm.assemble(
            store, "你是编码智能体。", TaskRecord(999, "当前", []),
            [{"role": "user", "content": "当前任务内容"}],
        )
        # [1] system 头
        assert messages[0]["role"] == "system"
        # [4] 当前段末尾
        assert messages[-1]["content"] == "当前任务内容"
        # 卡片/占位区（滑出窗口的 13 任务）
        placeholders = [
            m for m in messages
            if str(m.get("content", "")).startswith(
                ("[任务卡片]", "[历史任务]")
            )
        ]
        assert len(placeholders) >= 11
        # 尾部原文（最近 2 任务）
        tail_orig = [
            m for m in messages if "x" * 50 in str(m.get("content", ""))
        ]
        assert len(tail_orig) >= 2
        # 压缩后总量远小于原始
        assert stats["context_tokens"] < 30000
        assert stats["threshold"] == 112000

    @pytest.mark.asyncio
    async def test_current_task_never_compressed(self):
        """尾部保护: current_task_ids 含当前任务——其消息永不进 Prune。"""
        store = SessionStore()
        for i in range(20):
            store.archive_task(100 + i, f"任务{i}", [
                {"role": "tool", "content": f"工具{i}" + "z" * 8000},
            ])
        # 给最近任务一个特殊标记
        store.tasks[-1].messages[0]["content"] = "MUST_KEEP_" + "z" * 8000
        keep_id = store.tasks[-1].task_id
        c = ContextCompressor("qwen/deepseek-v4-pro")
        await c.compress(store, current_task_ids=[keep_id])
        kept = any(
            "MUST_KEEP" in m["content"]
            for t in store.tasks if t.task_id == keep_id
            for m in t.messages
        )
        assert kept, "当前任务原文必须保留"
