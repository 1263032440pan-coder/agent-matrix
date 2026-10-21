"""V1.4e 删除链测试: agent_instance + acp_message 进级联清单。"""

import json

import pytest
from sqlalchemy import func, select
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
from project_runtime.project_state.deletion import ProjectDeletionService
from project_runtime.project_state.enums import (
    AutomationMode,
    TaskStatus,
)
from project_runtime.project_state.models import Project, Task
from project_runtime.worker.agent_models import (
    AcpMessage,
    AgentDefinition,
    AgentInstance,
    AgentStatus,
)
from project_runtime.worker.registry import seed_builtin_templates


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/del.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _count(eng, model) -> int:
    async with AsyncSession(eng) as s:
        return (
            await s.execute(select(func.count()).select_from(model))
        ).scalar_one()


@pytest.mark.asyncio
async def test_project_with_agents_deletes_clean(dbengine):
    """带实例+ACP 消息的项目删除不再 FK 断链（V1.4e 修复）。"""
    eng = dbengine
    # seed: 项目 + 任务 + 模板 + BUSY 实例（current_task_id FK task）
    async with AsyncSession(eng) as s:
        p = Project(name="del", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await seed_builtin_templates(s)
        t = Task(project_id=pid, name="任务1", status=TaskStatus.READY)
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()
    async with AsyncSession(eng) as s:
        d = (
            await s.execute(
                select(AgentDefinition).where(
                    AgentDefinition.agent_id == "coder"
                )
            )
        ).scalar_one()
        inst = AgentInstance(
            definition_id=d.id, project_id=pid,
            status=AgentStatus.BUSY, current_task_id=tid,
        )
        s.add(inst)
        await s.flush()
        # ACP 双向引用（from+to 都是本项目实例——注意部分唯一索引:
        # 同项目同模板只许一个活跃实例 → 第二实例用 analyst 模板）
        d2 = (
            await s.execute(
                select(AgentDefinition).where(
                    AgentDefinition.agent_id == "analyst"
                )
            )
        ).scalar_one()
        inst2 = AgentInstance(
            definition_id=d2.id, project_id=pid, status=AgentStatus.IDLE,
        )
        s.add(inst2)
        await s.flush()
        s.add(AcpMessage(
            msg_type="request", project_id=pid,
            from_agent_instance_id=inst.id,
            to_agent_instance_id=inst2.id,
            payload=json.dumps({"q": 1}),
        ))
        await s.commit()

    # 删除（此前在此 FK 断链回滚）
    async with AsyncSession(eng) as s:
        result = await ProjectDeletionService(s).delete(pid)
        await s.commit()
    tables = result.get("tables") or result
    assert (tables.get("project") if isinstance(tables, dict) else None) == 1
    # agent 两表在删除计数里
    assert "agent_instance" in tables and "acp_message" in tables

    # 三表断言: 实例/ACP 清零; 模板保留（BUILT_IN 跨项目）
    assert await _count(eng, AgentInstance) == 0
    assert await _count(eng, AcpMessage) == 0
    assert await _count(eng, AgentDefinition) == 5  # 内置保留


@pytest.mark.asyncio
async def test_acp_orphan_refs_cleared(dbengine):
    """跨项目脏引用: ACP 消息 from 指向本项目但 project_id 挂错——也清。"""
    eng = dbengine
    async with AsyncSession(eng) as s:
        p1 = Project(name="A", automation_mode=AutomationMode.FULL_AUTO)
        p2 = Project(name="B", automation_mode=AutomationMode.FULL_AUTO)
        s.add_all([p1, p2])
        await s.flush()
        await seed_builtin_templates(s)
        t = Task(project_id=p1.id, name="t", status=TaskStatus.READY)
        s.add(t)
        await s.flush()
        d = (
            await s.execute(
                select(AgentDefinition).where(
                    AgentDefinition.agent_id == "coder"
                )
            )
        ).scalar_one()
        ia = AgentInstance(
            definition_id=d.id, project_id=p1.id,
            status=AgentStatus.IDLE,
        )
        ib = AgentInstance(
            definition_id=d.id, project_id=p2.id,
            status=AgentStatus.IDLE,
        )
        s.add_all([ia, ib])
        await s.flush()
        # ACP 行 project_id 错挂 B，但 from 指向 A 的实例（历史脏数据形态）
        s.add(AcpMessage(
            msg_type="inform", project_id=p2.id,
            from_agent_instance_id=ia.id,
            to_agent_instance_id=ib.id,
            payload=None,
        ))
        pid_a, pid_b = p1.id, p2.id  # commit 前快照（ORM 过期老坑）
        await s.commit()

    # 删 A → ia 被删 → 脏 ACP 行经 from 引用也被清; B 完好
    async with AsyncSession(eng) as s:
        await ProjectDeletionService(s).delete(pid_a)
        await s.commit()

    assert await _count(eng, AcpMessage) == 0  # 唯一消息两端涉 A → 清
    async with AsyncSession(eng) as s:
        n_b = (
            await s.execute(
                select(func.count()).select_from(AgentInstance).where(
                    AgentInstance.project_id == pid_b
                )
            )
        ).scalar_one()
    assert n_b == 1  # B 项目实例不动
