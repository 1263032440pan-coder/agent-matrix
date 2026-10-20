"""Agents API（20号规范批次 1——§9 UI 数据面）.

- GET  /api/projects/{id}/agents        实例实时状态（UI 面板 5s 轮询）
- GET  /api/agent-definitions           模板列表（含来源徽章数据）
- POST /api/agent-definitions           用户创建模板（stage_affinity 九词校验）
- POST /api/projects/{id}/agents/{aid}/pause | resume | terminate  生命周期操作
- GET  /api/projects/{id}/agents/{aid}/acp  ACP 消息流（批次 2 详情页用，先占位）
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import engine
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentStatus,
)
from project_runtime.worker.registry import (
    STANDARD_STAGES,
    AgentFSM,
    IllegalAgentTransitionError,
)


async def get_session():
    async with AsyncSession(engine) as session:
        yield session

router = APIRouter(prefix="/api", tags=["agents"])


def _def_serialize(d: AgentDefinition) -> dict:
    return {
        "id": d.id,
        "agent_id": d.agent_id,
        "name": d.name,
        "role_description": d.role_description,
        "model_config_name": d.model_config_name,
        "plugins": d.plugins or [],
        "skills": d.skills or [],
        "stage_affinity": d.stage_affinity or [],
        "source": d.source.value,
        "scope": d.scope.value,
        "enabled": d.enabled,
    }


def _inst_serialize(i: AgentInstance, d: AgentDefinition | None) -> dict:
    return {
        "id": i.id,
        "definition_id": i.definition_id,
        "agent_id": d.agent_id if d else None,
        "name": d.name if d else None,
        "project_id": i.project_id,
        "status": i.status.value,
        "current_task_id": i.current_task_id,
        "fail_count": i.fail_count,
        "context_tokens": i.context_tokens,
        "context_threshold": i.context_threshold,
    }


@router.get("/projects/{project_id}/agents")
async def list_project_agents(
    project_id: int, session: AsyncSession = Depends(get_session)
):
    """实例实时状态（UI 面板数据源——含上下文压力条两字段）。"""
    stmt = (
        select(AgentInstance, AgentDefinition)
        .join(AgentDefinition, AgentInstance.definition_id == AgentDefinition.id)
        .where(AgentInstance.project_id == project_id)
        .order_by(AgentInstance.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        _inst_serialize(inst, d) for inst, d in rows
    ]


@router.get("/agent-definitions")
async def list_definitions(session: AsyncSession = Depends(get_session)):
    stmt = select(AgentDefinition).order_by(AgentDefinition.id)
    return [
        _def_serialize(d) for d in
        (await session.execute(stmt)).scalars().all()
    ]


@router.post("/agent-definitions", status_code=201)
async def create_definition(
    body: dict, session: AsyncSession = Depends(get_session)
):
    """用户创建模板（§2.2 路径 2）——stage_affinity 限九标准词。"""
    agent_id = str(body.get("agent_id") or "").strip()
    name = str(body.get("name") or "").strip()
    if not agent_id or not name:
        raise HTTPException(422, "agent_id 与 name 必填")
    exists = (
        await session.execute(
            select(AgentDefinition).where(AgentDefinition.agent_id == agent_id)
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, f"模板已存在: {agent_id}")
    affinity = body.get("stage_affinity") or []
    bad = [a for a in affinity if a not in STANDARD_STAGES]
    if bad:
        raise HTTPException(
            422, f"stage_affinity 只允许标准词: {bad}（允许: {list(STANDARD_STAGES)}）"
        )
    d = AgentDefinition(
        agent_id=agent_id,
        name=name,
        role_description=body.get("role_description"),
        model_config_name=body.get("model_config_name"),
        plugins=body.get("plugins") or [],
        skills=body.get("skills") or [],
        stage_affinity=affinity,
        source="USER",
        scope="GLOBAL",
    )
    session.add(d)
    await session.commit()
    return _def_serialize(d)


async def _lifecycle(
    session: AsyncSession, project_id: int, instance_id: int,
    to: AgentStatus, actor: str = "USER",
) -> dict:
    inst = (
        await session.execute(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(404, f"agent instance not found: {instance_id}")
    try:
        await AgentFSM(session).transition(inst, to, actor=actor)
        await session.commit()
    except IllegalAgentTransitionError as e:
        raise HTTPException(409, str(e)) from e
    d = await session.get(AgentDefinition, inst.definition_id)
    return _inst_serialize(inst, d)


@router.post("/projects/{project_id}/agents/{instance_id}/pause")
async def pause_agent(
    project_id: int, instance_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await _lifecycle(session, project_id, instance_id, AgentStatus.PAUSED)


@router.post("/projects/{project_id}/agents/{instance_id}/resume")
async def resume_agent(
    project_id: int, instance_id: int,
    session: AsyncSession = Depends(get_session),
):
    """PAUSED→IDLE（恢复执行）。"""
    return await _lifecycle(session, project_id, instance_id, AgentStatus.IDLE)


@router.post("/projects/{project_id}/agents/{instance_id}/terminate")
async def terminate_agent(
    project_id: int, instance_id: int,
    session: AsyncSession = Depends(get_session),
):
    return await _lifecycle(
        session, project_id, instance_id, AgentStatus.TERMINATED
    )


@router.get("/projects/{project_id}/agents/{instance_id}/acp")
async def agent_acp_messages(
    project_id: int, instance_id: int,
    session: AsyncSession = Depends(get_session),
):
    """该 Agent 的 ACP 消息流（批次 2 详情页——数据面先通）。"""
    from project_runtime.worker.agent_models import AcpMessage

    inst = await session.get(AgentInstance, instance_id)
    if inst is None or inst.project_id != project_id:
        raise HTTPException(404, "agent instance not found")
    stmt = (
        select(AcpMessage)
        .where(
            AcpMessage.project_id == project_id,
            (AcpMessage.from_agent_instance_id == instance_id)
            | (AcpMessage.to_agent_instance_id == instance_id),
        )
        .order_by(AcpMessage.id)
    )
    msgs = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "msg_type": m.msg_type,
            "from": m.from_agent_instance_id,
            "to": m.to_agent_instance_id,
            "task_id": m.task_id,
            "payload": json.loads(m.payload) if m.payload else None,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]
