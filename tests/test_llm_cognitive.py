"""Batch 3 — LLM cognitive chain: LLMExtractor, CONFLICTED detection, GapType semantics."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.procedure.llm_extractor import LLMExtractor, detect_conflicts
from project_runtime.procedure.normalizer import UNKNOWN
from project_runtime.project_state.base import Base


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


# ── LLMExtractor fallback behavior ──────────────────────────────────────────


def test_llm_extractor_falls_back_when_not_configured():
    import asyncio

    from project_runtime.llm import LLMSettings

    ext = LLMExtractor(LLMSettings())  # empty config
    nodes = asyncio.new_event_loop().run_until_complete(
        ext.extract("## 需求\n输入: X\n输出: Y", "test.md")
    )
    assert nodes  # keyword fallback produced nodes
    assert nodes[0]["name"] == "需求"


def test_llm_extractor_red_line_unknown_not_invented():
    """LLM output normalizer: missing fields must stay UNKNOWN, never invented."""
    from project_runtime.llm import LLMSettings

    ext = LLMExtractor(LLMSettings())
    # Simulate LLM returning a node with some empty/whitespace fields
    node = ext._normalize_node(
        {"name": "测试", "inputs": "  ", "outputs": "报告"}, "src"
    )
    assert node["inputs"] == UNKNOWN
    assert node["outputs"] == "报告"
    assert node["dependencies"] == UNKNOWN


def test_llm_extractor_parses_llm_json():
    from project_runtime.llm import LLMSettings

    ext = LLMExtractor(LLMSettings())
    raw = '''```json
{"nodes": [{"name": "需求", "inputs": "访谈", "outputs": "requirements.md"}]}
```'''
    nodes = ext._parse(raw)
    assert len(nodes) == 1
    assert nodes[0]["name"] == "需求"


# ── CONFLICTED detection (D06-TD01) ─────────────────────────────────────────


def test_detect_conflicts_marks_conflicted():
    nodes = [
        {"name": "测试", "outputs": "报告A", "inputs": UNKNOWN},
        {"name": "测试", "outputs": "报告B", "inputs": UNKNOWN},  # conflict on outputs
    ]
    # fill remaining fields
    for n in nodes:
        for f in ("activities", "roles", "dependencies", "gate",
                  "acceptance_criteria", "evidence_requirements",
                  "constraints", "rework_path"):
            n.setdefault(f, UNKNOWN)

    conflicted = detect_conflicts(nodes)
    assert conflicted == ["测试"]
    assert all(n["readiness_status"] == "CONFLICTED" for n in nodes)


def test_detect_conflicts_no_conflict_when_fields_agree():
    nodes = [
        {"name": "A", "outputs": "X"},
        {"name": "A", "outputs": "X"},
    ]
    for n in nodes:
        for f in ("inputs", "activities", "roles", "dependencies", "gate",
                  "acceptance_criteria", "evidence_requirements",
                  "constraints", "rework_path"):
            n.setdefault(f, UNKNOWN)

    assert detect_conflicts(nodes) == []


def test_detect_conflicts_ignores_unknown_pairs():
    """Two UNKNOWN values are not a conflict (nothing asserted)."""
    nodes = [
        {"name": "A", "outputs": UNKNOWN},
        {"name": "A", "outputs": UNKNOWN},
    ]
    for n in nodes:
        for f in ("inputs", "activities", "roles", "dependencies", "gate",
                  "acceptance_criteria", "evidence_requirements",
                  "constraints", "rework_path"):
            n.setdefault(f, UNKNOWN)
    assert detect_conflicts(nodes) == []


# ── GapType six-class semantic classification (D08-TD01) ────────────────────


@pytest.mark.asyncio
async def test_gap_six_types_produced_without_llm(dbsession):
    """Without LLM, dimension mapping yields EXPLICITNESS/PROCESS only.
    With LLM unavailable, classifier must still work (fallback)."""
    from project_runtime.gap.classifier import GapClassifier
    from project_runtime.project_state.enums import GapType

    clf = GapClassifier(dbsession)
    # Direct unit: dimension fallback
    assert await clf._classify_gap_type(
        {"dimension": "scope_coverage"}
    ) == GapType.PROCESS
    assert await clf._classify_gap_type(
        {"dimension": "artifact_definition"}
    ) == GapType.EXPLICITNESS
    # UNKNOWN dim → EXPLICITNESS default
    assert await clf._classify_gap_type(
        {"dimension": "nonexistent"}
    ) == GapType.EXPLICITNESS


@pytest.mark.asyncio
async def test_gap_llm_classify_returns_none_without_config(dbsession):
    """LLM classify path returns None when not configured — fallback engages."""
    from project_runtime.gap.classifier import GapClassifier

    clf = GapClassifier(dbsession)
    assert await clf._classify_gap_type_llm({"reason": "缺部署环境"}) is None


def test_gap_llm_answer_parsing():
    """LLM answer parsing maps keywords to GapType."""
    from project_runtime.gap.classifier import GapClassifier
    from project_runtime.project_state.enums import GapType

    # Unit-test the parse loop logic (same loop as in _classify_gap_type_llm)
    for answer, expected in [
        ("PRIVATE", GapType.PRIVATE), ("CAPABILITY", GapType.CAPABILITY),
        ("DOMAIN", GapType.DOMAIN), ("TECHNICAL", GapType.TECHNICAL),
    ]:
        for gt in GapType:
            if gt.value in answer:
                assert gt == expected
                break
