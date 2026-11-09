"""O3：OfficeSpec v1 规范化与 V0 兼容。"""

from __future__ import annotations


def test_normalize_pptx_v0_gets_spec_version_1_and_default_theme():
    from project_runtime.office.spec import normalize_pptx_spec

    out, degraded = normalize_pptx_spec({
        "title": "Deck",
        "slides": [{"title": "A", "bullets": ["x"]}],
    })
    assert out["spec_version"] == "1"
    assert out["theme"]["preset"] == "business-blue"
    assert out["slides"][0]["layout"] == "bullets"
    assert degraded == []


def test_normalize_docx_blocks_from_paragraphs():
    from project_runtime.office.spec import normalize_docx_spec

    out, _ = normalize_docx_spec({
        "title": "报告",
        "paragraphs": ["一段", {"text": "二级", "heading": 2}],
    })
    assert out["spec_version"] == "1"
    kinds = [b["type"] for b in out["blocks"]]
    assert "heading" in kinds
    assert "paragraph" in kinds


def test_normalize_pptx_charts_degraded():
    from project_runtime.office.spec import normalize_pptx_spec

    out, degraded = normalize_pptx_spec({
        "spec_version": "1",
        "slides": [{"title": "C", "layout": "bullets", "chart": {"type": "pie"}}],
    })
    assert "chart" in degraded
    assert "chart" not in out["slides"][0]
