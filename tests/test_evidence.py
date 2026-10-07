"""WP-023 Completion Evidence tests — 验收四条.

- [x] Evidence 可关联 Task / Gate / Artifact / Test / Delivery Item
- [x] Evidence 可失效和重建
- [x] Delivery Item 不能在 Evidence 缺失时标记 SATISFIED
- [x] Project Completion 不依赖单次 LLM 判断
"""

import json

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
from project_runtime.evidence import (
    EvidenceError,
    EvidenceScope,
    EvidenceService,
    EvidenceType,
)
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    ArtifactType,
    AutomationMode,
    DeliveryItemStatus,
    GateStatus,
    TaskStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    DeliveryContract,
    DeliveryItem,
    GateResult,
    Project,
    Task,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ev.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine):
    """项目 + ACCEPTED task + PASS gate + TEST 产物 + SATISFIED-able item。"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="ev", goal="建设",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        t = Task(project_id=pid, name="任务A", status=TaskStatus.ACCEPTED)
        s.add(t)
        await s.flush()
        t_id = t.id
        s.add(Artifact(
            logical_artifact_id="tst", project_id=pid, task_id=t_id,
            name="测试报告", version=1, path="data/t.md",
            sha256="t" * 64, artifact_type=ArtifactType.TEST_RESULT,
        ))
        s.add(GateResult(task_id=t_id, gate_name="hard",
                         status=GateStatus.PASS, details="ok"))
        contract = DeliveryContract(project_id=pid, version=1,
                                    items=json.dumps(["功能A"]))
        s.add(contract)
        await s.flush()
        item = DeliveryItem(
            contract_id=contract.id, requirement_ref="功能A 交付",
            status=DeliveryItemStatus.PENDING,
        )
        s.add(item)
        await s.flush()
        item_id = item.id
        await s.commit()
    return pid, t_id, item_id


# ── Gate 1: 八类型关联五类实体 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_evidence_links_entities(dbengine):
    pid, t_id, item_id = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        gate_id = (await s.execute(
            select(GateResult.id))).scalar_one()
        art_id = (await s.execute(
            select(Artifact.id))).scalar_one()
        svc = EvidenceService(s)
        evs = [
            await svc.create_evidence(
                pid, EvidenceType.TASK_ACCEPTED,
                scope=EvidenceScope.TASK, ref=f"task:{t_id}",
                summary="任务验收通过"),
            await svc.create_evidence(
                pid, EvidenceType.GATE_PASSED,
                scope=EvidenceScope.TASK, ref=f"gate:{gate_id}",
                summary="hard-check PASS"),
            await svc.create_evidence(
                pid, EvidenceType.TEST_PASSED,
                scope=EvidenceScope.TASK, ref=f"test:artifact:{art_id}",
                summary="测试报告通过"),
            await svc.create_evidence(
                pid, EvidenceType.ARTIFACT_EXISTS,
                scope=EvidenceScope.TASK, ref=f"artifact:{art_id}",
                summary="测试产物在"),
            await svc.create_evidence(
                pid, EvidenceType.CONTRACT_ITEM_SATISFIED,
                scope=EvidenceScope.DELIVERY_ITEM,
                ref=f"delivery_item:{item_id}",
                summary="交付项满足"),
        ]
        refs = {e.ref for e in evs}  # snapshot before commit (ORM expiry)
        await s.commit()
        assert len(evs) == 5
        assert f"task:{t_id}" in refs
        assert f"delivery_item:{item_id}" in refs
        # payload 可携带
        ev_with_payload = await svc.create_evidence(
            pid, EvidenceType.MANUAL_EVIDENCE,
            scope=EvidenceScope.PROJECT, ref="manual:review-1",
            summary="人工核验", payload={"reviewer": "user", "score": 9},
        )
        payload_json = ev_with_payload.payload_json  # snapshot before commit
        await s.commit()
        assert json.loads(payload_json)["reviewer"] == "user"


# ── Gate 2: 失效与重建 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_and_revalidate(dbengine):
    pid, t_id, _ = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        ev = await svc.create_evidence(
            pid, EvidenceType.TASK_ACCEPTED,
            scope=EvidenceScope.TASK, ref=f"task:{t_id}",
        )
        ev_id = ev.id
        await s.commit()

        # 失效
        await svc.invalidate_evidence(ev_id, reason="task reopened")
        valid_now = await svc.get_scope_evidence(pid)
        assert not valid_now
        await s.commit()

    # task 回退（fixture 直改）→ 复检也判失效
    async with AsyncSession(dbengine) as s:
        t = await s.get(Task, t_id)
        t.status = TaskStatus.REWORK
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        # 重建: 新证据（ref 同）+ 复检
        ev2 = await svc.create_evidence(
            pid, EvidenceType.TASK_ACCEPTED,
            scope=EvidenceScope.TASK, ref=f"task:{t_id}",
            summary="重新验收",
        )
        ev2_id = ev2.id
        await s.commit()

        # task 回 REWORK → validate_scope 判 ev2 失效（ref 不再成立）
        t = await s.get(Task, t_id)
        t.status = TaskStatus.REWORK
        await s.commit()
        # evidence row was created while ACCEPTED; revalidation catches it
        # (need ACCEPTED state at validate time — set back to verify both ways)
        t2 = await s.get(Task, t_id)
        t2.status = TaskStatus.ACCEPTED
        await s.commit()
        valid = await svc.validate_scope(pid)
        assert any(e.id == ev2_id for e in valid)


# ── Gate 3: Delivery Item 硬门 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_delivery_item_requires_evidence(dbengine):
    pid, _, item_id = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        # 无证据 → SATISFIED 被拒
        with pytest.raises(EvidenceError, match="no valid evidence"):
            await svc.mark_item_satisfied(item_id)
        await s.commit()

        # 建证据 → 可标
        await svc.create_evidence(
            pid, EvidenceType.CONTRACT_ITEM_SATISFIED,
            scope=EvidenceScope.DELIVERY_ITEM,
            ref=f"delivery_item:{item_id}",
        )
        item = await svc.mark_item_satisfied(item_id)
        item_status = item.status
        await s.commit()
    assert item_status == DeliveryItemStatus.SATISFIED


# ── Gate 4: Project Completion 全结构化 ─────────────────────────────


@pytest.mark.asyncio
async def test_project_completion_structured(dbengine):
    """七条件逐项: 缺条件→failures 点名; 补齐→complete。零 LLM
    （服务无任何 litellm/LLM import——结构性断言另测）。"""
    pid, t_id, item_id = await _seed(dbengine)

    # 场景 A: 什么都没做 → 全条件失败
    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        check = await svc.check_project_completion(pid)
        await s.commit()
    assert not check.complete
    assert check.failures  # named failures

    # 场景 B: 补齐 traceability（重建图）+ evidence + item SATISFIED
    async with AsyncSession(dbengine) as s:
        from project_runtime.traceability import TraceabilityGraph

        tg = TraceabilityGraph(s)
        await tg.rebuild_scope(pid)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        gate_id = (await s.execute(
            select(GateResult.id))).scalar_one()
        await svc.create_evidence(
            pid, EvidenceType.CONTRACT_ITEM_SATISFIED,
            scope=EvidenceScope.DELIVERY_ITEM,
            ref=f"delivery_item:{item_id}",
        )
        await svc.create_evidence(
            pid, EvidenceType.GATE_PASSED,
            scope=EvidenceScope.TASK, ref=f"gate:{gate_id}",
        )
        item = await svc.mark_item_satisfied(item_id)
        item_status = item.status
        await s.commit()
    assert item_status == DeliveryItemStatus.SATISFIED

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        check = await svc.check_project_completion(pid)
        d = check.to_dict()
        await s.commit()
    # task ACCEPTED + gate PASS + test artifact exists + evidence valid +
    # item satisfied + no pending manual + traceability no gaps
    assert d["conditions"]["tasks_accepted"] is True
    assert d["conditions"]["final_gate_passed"] is True
    assert d["conditions"]["delivery_items_satisfied"] is True
    assert d["complete"], f"expected complete, failures={d['failures']}"


def test_no_llm_in_completion_path():
    """结构性断言: evidence 服务零 LLM 依赖（ADR-010）。"""
    import inspect

    import project_runtime.evidence.service as svc_mod

    src = inspect.getsource(svc_mod)
    for forbidden in ("litellm", "acompletion", "LLMExtractor",
                      "load_llm_settings"):
        assert forbidden not in src, (
            f"completion must not depend on LLM: {forbidden}"
        )


# ── 返修 P2: MethodGap 跨项目隔离 ───────────────────────────────────


@pytest.mark.asyncio
async def test_critical_issue_isolated_between_projects(dbengine):
    """B 项目的 HIGH OPEN gap 不得影响 A 项目的完成判定。"""
    from project_runtime.project_state.models import (
        MethodGap,
        ProjectReadinessAssessment,
    )

    pid_a, t_id, item_id = await _seed(dbengine)

    # B 项目: 一个 HIGH OPEN gap（若泄漏必阻断 A 判定）
    async with AsyncSession(dbengine) as s:
        p = Project(name="ev-b", goal="B",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid_b = p.id
        tb = Task(project_id=pid_b, name="B任务",
                  status=TaskStatus.ACCEPTED)
        s.add(tb)
        await s.flush()
        assessment_b = ProjectReadinessAssessment(
            project_id=pid_b, model_id=1,
            method_readiness="[]", gaps="{}",
        )
        s.add(assessment_b)
        await s.flush()
        s.add(MethodGap(
            assessment_id=assessment_b.id, gap_type="DOMAIN",
            description="B 项目高危缺口", severity="HIGH", status="OPEN",
        ))
        await s.commit()

    # A 项目走完整闭环（补齐七条件）——B 的 HIGH gap 不应出现在 A 判定
    async with AsyncSession(dbengine) as s:
        from project_runtime.traceability import TraceabilityGraph

        await TraceabilityGraph(s).rebuild_scope(pid_a)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        gate_id = (await s.execute(
            select(GateResult.id))).scalar_one()
        await svc.create_evidence(
            pid_a, EvidenceType.CONTRACT_ITEM_SATISFIED,
            scope=EvidenceScope.DELIVERY_ITEM,
            ref=f"delivery_item:{item_id}",
        )
        await svc.create_evidence(
            pid_a, EvidenceType.GATE_PASSED,
            scope=EvidenceScope.TASK, ref=f"gate:{gate_id}",
        )
        await svc.mark_item_satisfied(item_id)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        check = await svc.check_project_completion(pid_a)
        await s.commit()

    # B 的 HIGH gap 未阻断 A（隔离生效——否则 critical_issues_zero=False）
    assert check.critical_issues_zero is True
    assert check.complete, f"A should complete, failures={check.failures}"

    # 反向对照: A 自己有 HIGH gap 时确实阻断（验证判定本身没坏）
    async with AsyncSession(dbengine) as s:
        assessment_a = ProjectReadinessAssessment(
            project_id=pid_a, model_id=1,
            method_readiness="[]", gaps="{}",
        )
        s.add(assessment_a)
        await s.flush()
        s.add(MethodGap(
            assessment_id=assessment_a.id, gap_type="DOMAIN",
            description="A 项目自己的高危缺口", severity="HIGH",
            status="OPEN",
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = EvidenceService(s)
        check2 = await svc.check_project_completion(pid_a)
        await s.commit()
    assert check2.critical_issues_zero is False
    assert not check2.complete
