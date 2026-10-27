"""返修单 V3 批B: SKILL_GAP / SKILL_DISCOVERY / 安装回落 / 方法论研究。

SYS-089~092 离线 stub 验收（真联网/真 GPT-Researcher 不进 CI）。
"""

from __future__ import annotations

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
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.event.models import RuntimeEvent, RuntimeEventType
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, GapType
from project_runtime.project_state.models import MethodGap, Project
from project_runtime.research.models import ResearchRecord


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3b.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        p = Project(name="v3b", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
    yield eng, pid
    await eng.dispose()


class TestB1SkillGap:
    @pytest.mark.asyncio
    async def test_emit_skill_gap_creates_event_and_research(self, dbengine):
        """SYS-089: skill miss → SKILL_GAP 事件 + OPEN ResearchRecord。"""
        from project_runtime.skills.discovery import emit_skill_gap

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            req = await emit_skill_gap(
                s,
                pid,
                domain_summary="政务一体化平台建设",
                skill_type="PROJECT_METHOD",
            )
            await s.commit()

        assert req["request_type"] == "SKILL_GAP"
        assert req["skill_type"] == "PROJECT_METHOD"
        assert req.get("research_id")

        async with AsyncSession(eng) as s:
            evs = (
                await s.execute(
                    select(RuntimeEvent).where(
                        RuntimeEvent.project_id == pid,
                        RuntimeEvent.event_type
                        == RuntimeEventType.SKILL_GAP.value,
                    )
                )
            ).scalars().all()
            assert len(evs) >= 1
            payload = json.loads(evs[0].payload or "{}")
            assert payload.get("skill_type") == "PROJECT_METHOD"

            rec = await s.get(ResearchRecord, req["research_id"])
            assert rec is not None
            assert rec.research_kind == "SKILL_DISCOVERY"
            assert rec.status == "OPEN"

    @pytest.mark.asyncio
    async def test_read_skill_stages_miss_emits_gap(
        self, dbengine, monkeypatch,
    ):
        """selector 返回 None 不再静默——发 SKILL_GAP。"""
        from project_runtime.procedure import skill_planning as sp
        from project_runtime.skills.selector import SkillSelector

        eng, pid = dbengine

        async def _none(*_a, **_k):
            return None

        monkeypatch.setattr(SkillSelector, "select", _none)

        async with AsyncSession(eng) as s:
            stages = await sp.read_skill_stages(
                s, pid, doc_text="做一个政务审批系统",
            )
            await s.commit()
        assert stages is None

        async with AsyncSession(eng) as s:
            recs = (
                await s.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.project_id == pid,
                        ResearchRecord.research_kind == "SKILL_DISCOVERY",
                    )
                )
            ).scalars().all()
            assert len(recs) >= 1


class TestB2SkillDiscovery:
    def test_parse_skill_md_metadata(self):
        """SYS-090: 候选对齐 SKILL.md 元数据字段。"""
        from project_runtime.skills.discovery import parse_skill_md_metadata

        md = """---
name: ppt-master
description: Generate presentation decks
when_to_use: When user needs PPT
license: MIT
---
# Body
"""
        meta = parse_skill_md_metadata(md)
        assert meta["name"] == "ppt-master"
        assert meta["description"]
        assert meta["when_to_use"]
        assert meta["license"] == "MIT"

    @pytest.mark.asyncio
    async def test_discovery_from_search_hits(self, dbengine, monkeypatch):
        """搜索结果抽出 SKILL.md 元数据候选（无执行物）。"""
        from project_runtime.skills import discovery as disc

        eng, pid = dbengine

        async def _fake_search(_self, query: str):
            return [{
                "source": "https://example.com/skills/foo/SKILL.md",
                "title": "foo skill",
                "content": (
                    "name: foo-method\n"
                    "description: Domain method pack\n"
                    "when_to_use: domain projects\n"
                    "license: Apache-2.0\n"
                ),
            }]

        monkeypatch.setattr(
            "project_runtime.research.executor.WebResearchExecutor._search",
            _fake_search,
        )

        async with AsyncSession(eng) as s:
            candidates = await disc.run_skill_discovery(
                s, pid, question="政务 项目方法 skill SKILL.md",
            )
        assert candidates
        assert candidates[0]["name"]
        assert "scripts" not in candidates[0]  # 不含执行物


class TestB3InstallHook:
    @pytest.mark.asyncio
    async def test_no_candidates_falls_to_user_input(self, dbengine):
        """SYS-091: 无候选 → REQUEST_USER_INPUT。"""
        from project_runtime.skills.discovery import (
            route_discovery_candidates,
        )

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            out = await route_discovery_candidates(s, pid, [])
        assert out["request_type"] == "REQUEST_USER_INPUT"
        assert "安装" in out.get("reason", "") or "来源" in out.get(
            "reason", ""
        )

    @pytest.mark.asyncio
    async def test_candidates_enter_install_pipeline_stub(
        self, dbengine, monkeypatch,
    ):
        """命中候选 → 进安装闸钩子（批C 未就绪时 stub 拒收→USER_INPUT）。"""
        from project_runtime.skills import discovery as disc

        eng, pid = dbengine

        async def _reject(_cands, session=None):
            return {"installed": [], "rejected": _cands}

        monkeypatch.setattr(disc, "auto_install_candidates", _reject)

        async with AsyncSession(eng) as s:
            out = await disc.route_discovery_candidates(
                s, pid,
                [{"name": "x", "description": "d", "source_url": "u"}],
            )
        assert out["request_type"] == "REQUEST_USER_INPUT"

    @pytest.mark.asyncio
    async def test_private_blocks_discovery(self, dbengine):
        """PRIVATE 材料域零外发——不建 SKILL_DISCOVERY。"""
        from project_runtime.project_state.models import (
            ProjectProcedureModel,
            ProjectReadinessAssessment,
        )
        from project_runtime.skills.discovery import emit_skill_gap

        eng, pid = dbengine
        async with AsyncSession(eng) as s:
            model = ProjectProcedureModel(project_id=pid, version=1)
            s.add(model)
            await s.flush()
            assess = ProjectReadinessAssessment(
                project_id=pid,
                model_id=model.id,
                method_readiness="{}",
            )
            s.add(assess)
            await s.flush()
            s.add(MethodGap(
                assessment_id=assess.id,
                description="内部凭据缺失",
                gap_type=GapType.PRIVATE,
                status="OPEN",
            ))
            await s.flush()
            req = await emit_skill_gap(
                s, pid,
                domain_summary="内部系统",
                skill_type="PROJECT_METHOD",
            )
            await s.commit()

        assert req["request_type"] == "SKILL_GAP"
        assert req.get("network_blocked") is True
        assert req.get("research_id") is None


class TestB4Methodology:
    @pytest.mark.asyncio
    async def test_methodology_structured_report(
        self, dbengine, monkeypatch,
    ):
        """SYS-092: METHODOLOGY_RESEARCH → 结构化阶段报告。"""
        from project_runtime.skills import discovery as disc

        eng, pid = dbengine

        async def _fake_engine(question: str):
            return {
                "summary": "政务建设流程要点",
                "stages": [
                    {"name": "需求调研", "outputs": ["需求说明书"],
                     "dependencies": []},
                    {"name": "方案设计", "outputs": ["方案PPT"],
                     "dependencies": ["需求调研"]},
                ],
            }

        monkeypatch.setattr(disc, "run_gpt_researcher", _fake_engine)

        async with AsyncSession(eng) as s:
            report = await disc.run_methodology_research(
                s, pid, question="政务系统 建设流程 规范",
            )
            await s.commit()

        assert report["stages"]
        assert report["stages"][0]["name"]
        async with AsyncSession(eng) as s:
            rec = (
                await s.execute(
                    select(ResearchRecord).where(
                        ResearchRecord.project_id == pid,
                        ResearchRecord.research_kind
                        == "METHODOLOGY_RESEARCH",
                        ResearchRecord.status == "ANSWERED",
                    )
                )
            ).scalar_one()
            findings = json.loads(rec.findings_json or "{}")
            assert findings.get("stages")
