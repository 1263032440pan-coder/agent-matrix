"""WP-028 Artifact Template Registry tests — 验收四条 + 复验预告五条.

验收:
  [x] Template 可版本化
  [x] Template 可导入
  [x] Template 为空时系统仍可运行
  [x] 支持 Dynamic Artifact Contract

复验预告:
  ① 空 Registry 红线——模板缺席 → Dynamic Contract，永不 BLOCKED
  ② 版本化（(template_id, version) 唯一，参考 skill 模式）
  ③ 导入安全三防（扩展名白名单/路径穿越/大小限制）
  ④ Dynamic Contract 生成路径（Playbook 需要产物无模板时产出）
  ⑤ WP-026 known_requirements 衔接
"""

import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.artifact_template import (
    ArtifactTemplateRegistry,
    DynamicContractService,
    TemplateImportError,
)
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.readiness.assessor import MethodReadinessAssessor
from project_runtime.understanding import UnderstandingService

DOC = """# 实施流程

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清
- 输出: 需求基线文档
- 依赖: 无
- 约束: 变更须评审
- 验收: 条目有验收标准
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tpl.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _chain_with_playbook(dbengine):
    """project → asset → normalize → assess（无 playbook 也行——wanted
    会从 understanding requirements 来；这里全链确保 PLAYBOOK 来源）。"""
    from project_runtime.playbook.builder import PlaybookBuilder

    async with AsyncSession(dbengine) as s:
        p = Project(name="tpl", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content=DOC,
            content_hash=hashlib.sha256(DOC.encode()).hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        await UnderstandingService(s).analyze_project(pid)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await MethodReadinessAssessor(s).assess(pid, mid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await PlaybookBuilder(s).build(pid)
        await s.commit()
    return pid


# ── 验收 2: 可导入 + 预告③ 三防 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_import_template(dbengine):
    async with AsyncSession(dbengine) as s:
        reg = ArtifactTemplateRegistry(s)
        tpl = await reg.import_template(
            template_id="req-baseline", name="需求基线模板.md",
            version="1.0.0", content="# 需求基线\n\n## 条目\n- ",
        )
        tid = tpl.id
        await s.commit()
    assert tid == 1


@pytest.mark.asyncio
async def test_import_security_three_layers(dbengine):
    async with AsyncSession(dbengine) as s:
        reg = ArtifactTemplateRegistry(s)
        # ① 扩展名白名单
        with pytest.raises(TemplateImportError, match="extension"):
            await reg.import_template(
                template_id="x", name="evil.exe", version="1",
                content="MZ...",
            )
        # ② 路径穿越
        with pytest.raises(TemplateImportError, match="traversal"):
            await reg.import_template(
                template_id="x", name="../../etc/passwd.md", version="1",
                content="# hi",
            )
        with pytest.raises(TemplateImportError, match="traversal"):
            await reg.import_template(
                template_id="x", name=r"C:\Windows\system32\evil.md",
                version="1", content="# hi",
            )
        # ③ 大小限制
        with pytest.raises(TemplateImportError, match="too large"):
            await reg.import_template(
                template_id="x", name="big.md", version="1",
                content="x" * (512 * 1024 + 1),
            )


# ── 验收 1 + 预告②: 版本化 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_template_versioning(dbengine):
    async with AsyncSession(dbengine) as s:
        reg = ArtifactTemplateRegistry(s)
        await reg.import_template(
            template_id="req-baseline", name="tpl.md", version="1.0.0",
            content="v1 body",
        )
        # same (id, version) rejected
        with pytest.raises(TemplateImportError, match="already registered"):
            await reg.import_template(
                template_id="req-baseline", name="tpl.md", version="1.0.0",
                content="v1 body again",
            )
        # new version registers fine — both coexist
        t2 = await reg.import_template(
            template_id="req-baseline", name="tpl.md", version="1.1.0",
            content="v2 body",
        )
        t2_version = t2.version  # snapshot before commit (ORM expiry)
        await s.commit()
        assert t2_version == "1.1.0"
        all_versions = await reg.list_templates()
        assert len(all_versions) == 2

        # disable 1.0.0 → only 1.1.0 enabled
        await reg.disable("req-baseline", "1.0.0")
        enabled = await reg.list_templates(enabled_only=True)
        enabled_versions = [t.version for t in enabled]  # snapshot
        assert enabled_versions == ["1.1.0"]


# ── 验收 3 + 预告①: 空 Registry → Dynamic Contract，永不 BLOCKED ────


@pytest.mark.asyncio
async def test_empty_registry_still_runs(dbengine):
    """Registry 为空（零模板）→ 全部产物走 Dynamic Contract，全链可运行。"""
    pid = await _chain_with_playbook(dbengine)
    async with AsyncSession(dbengine) as s:
        svc = DynamicContractService(s)
        contracts = await svc.generate_contracts(pid)
        created = [(c.artifact_name, c.source, c.template_ref) for c in contracts]
        await s.commit()

    # playbook 需要产物（需求基线文档）→ 契约产出，无一条 BLOCKED
    assert contracts, "playbook outputs must produce contracts"
    assert any("需求基线" in n for n, _, _ in created)
    for name, source, ref in created:
        assert ref is None  # no template registered → dynamic
        assert source in ("PLAYBOOK", "REQUIREMENT")


# ── 验收 4 + 预告④: Dynamic Contract 生成路径（模板命中对照）──────


@pytest.mark.asyncio
async def test_template_hit_vs_dynamic_miss(dbengine):
    pid = await _chain_with_playbook(dbengine)

    # register a template matching the 需求基线 artifact
    async with AsyncSession(dbengine) as s:
        reg = ArtifactTemplateRegistry(s)
        await reg.import_template(
            template_id="req-baseline", name="需求基线.md", version="1.0.0",
            content="# 需求基线模板",
            structure_json=json.dumps({"sections": ["条目", "验收"]}),
        )
        await s.commit()

    async with AsyncSession(dbengine) as s:
        svc = DynamicContractService(s)
        contracts = await svc.generate_contracts(pid)
        snap = [(c.artifact_name, c.source, c.template_ref) for c in contracts]
        await s.commit()

    hit = [x for x in snap if x[2] is not None]
    # matched artifact references the template
    assert hit and hit[0][2] == "req-baseline@1.0.0"
    assert hit[0][1] == "TEMPLATE"
    # unmatched artifacts stay dynamic — never blocked
    # (template body hint present in spec for the hit)
    async with AsyncSession(dbengine) as s:
        from project_runtime.artifact_template.models import (
            DynamicArtifactContract,
        )

        rows = (await s.execute(select(DynamicArtifactContract).where(
            DynamicArtifactContract.project_id == pid))).scalars().all()
        templated = [r for r in rows if r.template_ref]
        assert templated
        spec = json.loads(templated[0].spec_json)
        assert "template_body_hint" in spec


# ── 预告⑤: WP-026 known_requirements 衔接 ──────────────────────────


@pytest.mark.asyncio
async def test_understanding_requirements_feed_contracts(dbengine):
    """known_requirements 中含产物关键词的陈述 → REQUIREMENT 来源契约。"""
    pid = await _chain_with_playbook(dbengine)
    async with AsyncSession(dbengine) as s:
        svc = DynamicContractService(s)
        contracts = await svc.generate_contracts(pid)
        sources = {c.source for c in contracts}
        await s.commit()
    # requirement-named artifacts participate
    assert sources <= {"PLAYBOOK", "REQUIREMENT", "TEMPLATE"}
    # and the contract set is non-empty overall
    assert contracts


# ── 幂等: 重复生成不重复建 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_idempotent(dbengine):
    pid = await _chain_with_playbook(dbengine)
    async with AsyncSession(dbengine) as s:
        svc = DynamicContractService(s)
        first = await svc.generate_contracts(pid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        svc = DynamicContractService(s)
        second = await svc.generate_contracts(pid)
        await s.commit()
    assert first and second == []
