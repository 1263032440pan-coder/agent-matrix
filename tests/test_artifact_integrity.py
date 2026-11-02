"""内容完整性校验 + Hermes 工具名 nudge。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.procedure.artifact_integrity import (
    check_paths_integrity,
    integrity_repair_nudge,
)


def test_plaintext_pptx_fails(tmp_path: Path):
    p = tmp_path / "a.pptx"
    p.write_text("This is a placeholder. Run: python build.py", encoding="utf-8")
    fails = check_paths_integrity(tmp_path, ["a.pptx"])
    assert fails and fails[0]["kind"] in {
        "plaintext_disguised_as_binary",
        "pptx_too_small",
        "pptx_not_ooxml",
    }


def test_minimal_real_pptx_passes(tmp_path: Path):
    from pptx import Presentation

    out = tmp_path / "ok.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Hello"
    prs.save(str(out))
    assert check_paths_integrity(tmp_path, ["ok.pptx"]) == []


def test_nudge_mentions_office_tools():
    text = integrity_repair_nudge(
        0, [{"path": "x.pptx", "kind": "bad", "detail": "假"}],
    )
    assert "office_pptx" in text
    assert "write_file" in text
