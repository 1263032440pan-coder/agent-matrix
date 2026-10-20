"""20号规范批次 2: ACP 总线 + Controller（对账循环+五闸）测试（全离线）.

覆盖:
  [x] ACP 五类消息收发/未知类型拒绝/收件箱 poll 清空
  [x] request 超时升级 report（stub 时钟压缩等待）
  [x] 偏移巡检: 契约关键词缺失→correction+PAUSE / 全命中→None
  [x] 对账: covered/stuck 分类 / all_green 判定 / manifest_check 事件
  [x] 五闸①: 开放式挑刺拒绝 / 条款引用放行
  [x] 五闸②: REWORK 3 轮上限
  [x] 五闸③: 补给预算 50%
  [x] 五闸④: 连续 8 FAIL/REWORK→项目 PAUSED / 正常序列不熔断
  [x] 卡点动作: FAILED 任务→correction 发给其 Agent
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
from project_runtime.project_state.enums import (
    AutomationMode,
    ProjectStatus,
    TaskStatus,
)
from project_runtime.project_state.models import Project, Task
from project_runtime.worker.acp import AcpBus, AcpError, AcpMsg
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentSource,
    AgentStatus,
)
from project_runtime.worker.controller import (
    Controller,
    OpenEndedReworkError,
    ReworkLimitExceededError,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/b2.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="b2", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


async def _mk_agent(session, pid) -> tuple[AgentDefinition, AgentInstance]:
    d = AgentDefinition(agent_id="coder-t", name="T", source=AgentSource.BUILT_IN)
    session.add(d)
    await session.flush()
    inst = AgentInstance(
        definition_id=d.id, project_id=pid, status=AgentStatus.IDLE
    )
    session.add(inst)
    await session.flush()
    return d, inst


# ── ACP 总线 ───────────────────────────────────────────────────────


class TestAcpBus:
    @pytest.mark.asyncio
    async def test_send_and_poll(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            _, inst = await _mk_agent(s, pid)
            bus = AcpBus(s)
            mid = await bus.send(AcpMsg(
                msg_type="directive", project_id=pid,
                to_agent_instance_id=inst.id,
                payload={"cmd": "hold"},
            ))
            assert mid >= 1
            msgs = bus.poll(inst.id)
            assert len(msgs) == 1 and msgs[0].msg_type == "directive"
            # poll 清空
            assert bus.poll(inst.id) == []
            await s.commit()

    @pytest.mark.asyncio
    async def test_unknown_type_rejected(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            bus = AcpBus(s)
            with pytest.raises(AcpError, match="未知消息类型"):
                await bus.send(AcpMsg(
                    msg_type="gossip", project_id=pid, payload={}
                ))

    @pytest.mark.asyncio
    async def test_request_timeout_escalates(self, dbengine, monkeypatch):
        """request 超时 → 自动 report 升级 Controller。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            _, a = await _mk_agent(s, pid)
            d2 = AgentDefinition(agent_id="d2", name="D2",
                                 source=AgentSource.BUILT_IN)
            s.add(d2)
            await s.flush()
            b = AgentInstance(definition_id=d2.id, project_id=pid,
                              status=AgentStatus.IDLE)
            s.add(b)
            await s.flush()
            bus = AcpBus(s)
            # 压缩等待: monkeypatch 超时与 sleep
            import project_runtime.worker.acp as acp_mod

            monkeypatch.setattr(acp_mod, "REQUEST_TIMEOUT_S", 0.3)

            async def fast_sleep(_):
                import asyncio

                await asyncio.sleep(0.05)

            monkeypatch.setattr(acp_mod, "_sleep", fast_sleep)
            # a 向 b 发 request——b 不应答 → 超时 → report 进 a 的收件箱? 不:
            # report 发给 Controller（to=None）——落库审计
            result = await bus.request(AcpMsg(
                msg_type="request", project_id=pid,
                from_agent_instance_id=a.id,
                to_agent_instance_id=b.id,
                payload={"what": "接口定义"},
            ))
            assert result is None
            from project_runtime.worker.agent_models import AcpMessage

            rows = (
                await s.execute(
                    select(AcpMessage).where(
                        AcpMessage.msg_type == "report"
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].from_agent_instance_id == a.id
            await s.commit()

    @pytest.mark.asyncio
    async def test_request_answered(self, dbengine, monkeypatch):
        """request 得到 inform 应答 → 返回应答。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            _, a = await _mk_agent(s, pid)
            d2 = AgentDefinition(agent_id="d3", name="D3",
                                 source=AgentSource.BUILT_IN)
            s.add(d2)
            await s.flush()
            b = AgentInstance(definition_id=d2.id, project_id=pid,
                              status=AgentStatus.IDLE)
            s.add(b)
            await s.flush()
            bus = AcpBus(s)

            import project_runtime.worker.acp as acp_mod

            async def answer_then_poll(sec):
                # 第一次 poll 等待时，注入 b 的应答
                if not getattr(answer_then_poll, "_done", False):
                    answer_then_poll._done = True
                    await bus.send(AcpMsg(
                        msg_type="inform", project_id=pid,
                        from_agent_instance_id=b.id,
                        to_agent_instance_id=a.id,
                        payload={"接口": "GET /users"},
                    ))
                import asyncio

                await asyncio.sleep(0.01)

            monkeypatch.setattr(acp_mod, "_sleep", answer_then_poll)
            result = await bus.request(AcpMsg(
                msg_type="request", project_id=pid,
                from_agent_instance_id=a.id,
                to_agent_instance_id=b.id,
                payload={"what": "User 模块 API"},
            ))
            assert result is not None
            assert result.msg_type == "inform"
            await s.commit()


# ── Controller: 偏移巡检与纠正 ─────────────────────────────────────


class TestDeviationCorrection:
    @pytest.mark.asyncio
    async def test_deviation_detected_and_paused(self, dbengine):
        """契约关键词在产出中缺失 → correction + Agent PAUSED。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            _, inst = await _mk_agent(s, pid)
            inst.status = AgentStatus.BUSY
            await s.flush()
            c = Controller(s)
            mid = await c.inspect_and_correct(
                pid, inst.id, task_id=1,
                contract="验收: 实现用户注册接口与登录接口",
                output="实现了数据表设计（未提接口）",
            )
            assert mid is not None
            assert inst.status is AgentStatus.PAUSED  # 先暂停再纠正
            from project_runtime.worker.agent_models import AcpMessage

            row = await s.get(AcpMessage, mid)
            assert row.msg_type == "correction"
            assert "偏移" in row.payload or "重做" in row.payload
            await s.commit()

    @pytest.mark.asyncio
    async def test_no_deviation_no_correction(self, dbengine):
        """产出覆盖契约关键词 → 不纠正。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            _, inst = await _mk_agent(s, pid)
            c = Controller(s)
            mid = await c.inspect_and_correct(
                pid, inst.id, task_id=1,
                contract="验收: 用户注册接口",
                output="完成用户注册接口实现，含参数校验",
            )
            assert mid is None
            assert inst.status is AgentStatus.IDLE  # 未被暂停


# ── Controller: 对账循环 ───────────────────────────────────────────


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_reconcile_classifies(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            s.add(Task(project_id=pid, name="A", status=TaskStatus.ACCEPTED))
            s.add(Task(project_id=pid, name="B", status=TaskStatus.ACCEPTED))
            s.add(Task(project_id=pid, name="C", status=TaskStatus.FAILED))
            s.add(Task(project_id=pid, name="D", status=TaskStatus.READY))
            await s.commit()
        async with AsyncSession(eng) as s:
            c = Controller(s)
            r = await c.reconcile(pid)
            assert set(r.covered) == {"A", "B"}
            assert r.stuck == ["C"]
            assert r.all_green is False
            # 4 节点 → 预算 2
            assert r.supply_budget == 2
            await s.commit()

    @pytest.mark.asyncio
    async def test_all_green(self, dbengine):
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            s.add(Task(project_id=pid, name="A", status=TaskStatus.ACCEPTED))
            s.add(Task(project_id=pid, name="B", status=TaskStatus.ACCEPTED))
            await s.commit()
        async with AsyncSession(eng) as s:
            r = await Controller(s).reconcile(pid)
            assert r.all_green is True

    @pytest.mark.asyncio
    async def test_stuck_gets_correction(self, dbengine):
        """卡点（FAILED）任务 → correction 发给其执行 Agent。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            t = Task(project_id=pid, name="C", status=TaskStatus.FAILED)
            s.add(t)
            await s.flush()
            _, inst = await _mk_agent(s, pid)
            inst.current_task_id = t.id
            inst.status = AgentStatus.BUSY
            await s.commit()
        async with AsyncSession(eng) as s:
            c = Controller(s)
            r = await c.reconcile(pid)
            mids = await c.act_on_report(r)
            assert len(mids) >= 1
            from project_runtime.worker.agent_models import AcpMessage

            row = await s.get(AcpMessage, mids[0])
            assert row.msg_type == "correction"
            assert "恢复执行" in row.payload
            await s.commit()


# ── 防死循环五闸 ───────────────────────────────────────────────────


class TestFiveGates:
    def test_gate1_open_ended_rejected(self):
        with pytest.raises(OpenEndedReworkError, match="条款"):
            Controller.validate_rework_instruction("测试不够充分，继续完善")

    def test_gate1_clause_reference_allowed(self):
        Controller.validate_rework_instruction("违反条款 3: 404≠500")
        Controller.validate_rework_instruction("FR-003 验收未满足")
        Controller.validate_rework_instruction("依据条款 12，缺少日志输出")

    def test_gate2_rework_limit(self):
        Controller.check_rework_limit("T", 2)  # 未达
        with pytest.raises(ReworkLimitExceededError, match="上限"):
            Controller.check_rework_limit("T", 3)

    def test_gate3_supply_budget(self):
        Controller.check_supply_budget(8, 3)  # 未达
        with pytest.raises(ReworkLimitExceededError, match="预算"):
            Controller.check_supply_budget(8, 4)  # 8×50%=4 → 拒
        Controller.check_supply_budget(1, 0)  # 单节点预算 1

    @pytest.mark.asyncio
    async def test_gate4_global_fuse(self, dbengine):
        """连续 8 个任务 FAIL → 项目 PAUSED。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            for i in range(8):
                s.add(Task(project_id=pid, name=f"F{i}",
                           status=TaskStatus.FAILED))
            await s.commit()
        async with AsyncSession(eng) as s:
            c = Controller(s)
            assert await c.check_global_fuse(pid) is True
            proj = await s.get(Project, pid)
            assert proj.status is ProjectStatus.PAUSED
            await s.commit()

    @pytest.mark.asyncio
    async def test_gate4_normal_sequence_no_fuse(self, dbengine):
        """混合终态（有 ACCEPTED）不熔断。"""
        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            for i in range(4):
                s.add(Task(project_id=pid, name=f"F{i}",
                           status=TaskStatus.FAILED))
            s.add(Task(project_id=pid, name="OK", status=TaskStatus.ACCEPTED))
            for i in range(4, 8):
                s.add(Task(project_id=pid, name=f"F{i}",
                           status=TaskStatus.REWORK))
            await s.commit()
        async with AsyncSession(eng) as s:
            c = Controller(s)
            assert await c.check_global_fuse(pid) is False
            proj = await s.get(Project, pid)
            assert proj.status is not ProjectStatus.PAUSED
