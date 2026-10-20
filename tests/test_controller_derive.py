"""返修-20号-V1.4 Phase 2 测试: Controller 派生白名单推导（全离线）.

覆盖（返修单 §2.4）:
  [x] 推导正/反: 开发环节派生 ⊆ coder 模板 plugins（硬门）
  [x] 契约含"架构图" → +arch_diagram；契约无安全词 → 仅上限+基线
  [x] shell/git 不经②获得（契约写了"执行 shell"也不加）
  [x] misc 环节 → 三能力基线
  [x] skills 恒 None
  [x] 数量闸: 超限返回 None
  [x] 幂等闸: 同签名二次派生复用不重建
  [x] scope=PROJECT + source=CONTROLLER_GENERATED
  [x] route-miss 派生链端到端: bind 无实例 → 派生 → provision → 命中
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
from project_runtime.project_state.enums import AutomationMode
from project_runtime.project_state.models import Project
from project_runtime.worker.agent_models import AgentSource
from project_runtime.worker.derive import (
    BASELINE_PLUGINS,
    controller_template_signature,
    derive_controller_whitelist,
    ensure_controller_template,
)
from project_runtime.worker.registry import BUILT_IN_TEMPLATES


def _builtin_plugins(agent_id: str) -> list[str] | None:
    for tpl in BUILT_IN_TEMPLATES:
        if tpl["agent_id"] == agent_id:
            return list(tpl["plugins"])
    return None


# ── 纯函数推导（§2.1 三段式）──────────────────────────────────────


class TestDerivePure:
    def test_dev_stage_within_ceiling(self):
        """开发环节派生 ⊆ coder 模板 plugins（硬门——上限语义）。"""
        coder = _builtin_plugins("coder")
        plugins, skills = derive_controller_whitelist(
            "开发实现", "实现登录功能", coder
        )
        assert set(plugins) <= set(coder)
        assert skills is None

    def test_contract_increment_arch_diagram(self):
        """契约含"架构图" → +arch_diagram（安全增量集内）。"""
        designer = _builtin_plugins("designer")  # designer 已含 arch_diagram
        plugins, _ = derive_controller_whitelist(
            "架构设计", "产出系统架构图与部署说明", designer
        )
        assert "arch_diagram" in plugins

    def test_contract_increment_from_baseline(self):
        """misc 环节 + 契约安全词 → 基线 + 增量（增量不越安全集）。"""
        plugins, _ = derive_controller_whitelist(
            "misc", "需要生成汇报 PPT 与待办清单", None
        )
        assert "slide_generator" in plugins
        assert "todo" in plugins
        assert set(plugins) <= set(BASELINE_PLUGINS) | {
            "slide_generator", "todo",
        }

    def test_shell_never_from_contract(self):
        """shell/git 不经②获得——契约明写'执行 shell'也不加（硬门）。"""
        plugins, _ = derive_controller_whitelist(
            "misc", "需要执行 shell 命令并 git 提交", None
        )
        assert "shell" not in plugins
        assert "git" not in plugins
        # 只拿到基线
        assert set(plugins) == set(BASELINE_PLUGINS)

    def test_shell_from_ceiling_only(self):
        """shell 只能经①环节上限获得（开发环节内置模板含 shell）。"""
        coder = _builtin_plugins("coder")
        plugins, _ = derive_controller_whitelist("开发实现", "", coder)
        assert "shell" in plugins  # 来自上限（coder 内置）
        assert "git" in plugins

    def test_misc_fallback_baseline(self):
        """无对应环节 → 三能力基线。"""
        plugins, skills = derive_controller_whitelist("misc", None, None)
        assert set(plugins) == set(BASELINE_PLUGINS)
        assert skills is None

    def test_baseline_always_present(self):
        """基线保底（旧库天花板缺基线的防御）。"""
        plugins, _ = derive_controller_whitelist(
            "开发实现", "", ["shell"]  # 异常旧值: 无 file_rw/web_search
        )
        assert "file_rw" in plugins and "web_search" in plugins

    def test_signature_stable(self):
        """幂等闸签名: 同 stage+能力词 → 同签名; 变化 → 变。"""
        s1 = controller_template_signature("开发实现", "需要 PPT")
        s2 = controller_template_signature("开发实现", "需要幻灯片汇报")
        s3 = controller_template_signature("测试验证", "需要 PPT")
        assert s1 == s2  # 能力词归一后同签名
        assert s1 != s3


# ── ensure_controller_template（双闸 + 审计 + 落库）───────────────


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dv.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="dv", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


class TestEnsureTemplate:
    @pytest.mark.asyncio
    async def test_creates_with_derived_whitelist(self, dbengine):
        """开发环节 route-miss → 派生模板（⊆ coder 上限）落库。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            d = await ensure_controller_template(
                s, pid, "开发实现", task_contract="实现用户模块"
            )
            # commit 前快照（ORM 过期老坑）
            dsource, dscope, dplugins, dskills = (
                d.source, d.scope, list(d.plugins), d.skills,
            )
            await s.commit()
        assert d is not None
        assert dsource is AgentSource.CONTROLLER_GENERATED
        assert dscope == "PROJECT"
        coder = _builtin_plugins("coder")
        assert set(dplugins) <= set(coder)
        assert dskills is None

    @pytest.mark.asyncio
    async def test_idempotent_same_signature(self, dbengine):
        """幂等闸: 同签名二次派生 → 复用（库内一行）。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            d1 = await ensure_controller_template(
                s, pid, "开发实现", task_contract="实现 A 模块"
            )
            d1id = d1.id
            await s.commit()
        async with AsyncSession(eng) as s:
            d2 = await ensure_controller_template(
                s, pid, "开发实现", task_contract="实现 B 模块"  # 同环节、无安全词差异
            )
            d2id = d2.id
            await s.commit()
        assert d1id == d2id  # 复用同一行

    @pytest.mark.asyncio
    async def test_cap_reached_returns_none(self, dbengine):
        """数量闸: 项目内派生模板超上限 → None（落老路径）。"""
        eng, pid = dbengine
        from project_runtime.worker.derive import CONTROLLER_TEMPLATE_CAP

        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import AgentDefinition

            for i in range(CONTROLLER_TEMPLATE_CAP):
                s.add(AgentDefinition(
                    agent_id=f"cap-{i}", name=f"c{i}",
                    source=AgentSource.CONTROLLER_GENERATED,
                    scope="PROJECT",
                ))
            await s.commit()
        async with AsyncSession(eng) as s:
            d = await ensure_controller_template(
                s, pid, "开发实现", task_contract=None
            )
            await s.commit()
        assert d is None

    @pytest.mark.asyncio
    async def test_audit_event_emitted(self, dbengine):
        """审计事件同事务直插（RH-03 铁律）——查库断言。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            await ensure_controller_template(
                s, pid, "开发实现", task_contract="实现 X"
            )
            await s.commit()
        import json

        from project_runtime.event.models import RuntimeEvent

        async with AsyncSession(eng) as s:
            rows = (
                await s.execute(
                    select(RuntimeEvent).where(
                        RuntimeEvent.phase == "CONTROLLER_DERIVE"
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].actor == "CONTROLLER"
            payload = json.loads(rows[0].payload or "{}")
            assert payload["ceiling_from"] == "coder"
            assert "plugins" in payload
            assert payload["rule"] == "①环节上限继承"


# ── route-miss 派生链端到端（bind_agent 内联）─────────────────────


class TestRouteMissChain:
    @pytest.mark.asyncio
    async def test_bind_derives_and_routes(self, dbengine):
        """端到端: 无实例 bind → 派生模板 → provision → bind 命中。"""
        eng, pid = dbengine
        from project_runtime.worker.lifecycle import bind_agent

        # 注意: bind_agent 内部 EventEngine 用全局引擎——审计事件
        # 写开发库是 best-effort（不阻断）; 本用例只断言派生+绑定语义
        async with AsyncSession(eng) as s:
            binding = await bind_agent(
                s, pid, task_id=1, task_type="开发实现",
                task_contract="实现登录接口",
            )
            await s.commit()
        assert binding is not None  # 派生链生效——bind 命中
        assert binding.tool_whitelist is not None
        coder = _builtin_plugins("coder")
        assert set(binding.tool_whitelist) <= set(coder)

        # 库里确实有派生模板 + 实例
        async with AsyncSession(eng) as s:
            from project_runtime.worker.agent_models import (
                AgentDefinition,
                AgentInstance,
            )

            defs = (
                await s.execute(
                    select(AgentDefinition).where(
                        AgentDefinition.source
                        == AgentSource.CONTROLLER_GENERATED
                    )
                )
            ).scalars().all()
            assert len(defs) == 1
            assert defs[0].scope == "PROJECT"
            insts = (
                await s.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == pid
                    )
                )
            ).scalars().all()
            assert len(insts) == 1
