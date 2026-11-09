"""O2：PDF 只能由 docx 转换（方案 §四 / V7–V8）。"""

from __future__ import annotations

import asyncio
from pathlib import Path


def test_office_pdf_rejects_spec():
    from project_runtime.office.providers import OfficePdfProvider

    async def _run(tmp: Path):
        p = OfficePdfProvider()
        return await p.invoke({
            "path": "out.pdf",
            "spec": {"title": "x", "paragraphs": ["a"]},
            "__ws_root": str(tmp),
        })

    # 即使有 soffice 也不该走到转换
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = asyncio.run(_run(Path(d)))
    assert out.get("ok") is False
    assert out.get("error_code") == "PDF_REQUIRES_DOCX"


def test_office_pdf_missing_source():
    from project_runtime.office.providers import OfficePdfProvider

    async def _run(tmp: Path):
        return await OfficePdfProvider().invoke({
            "path": "out.pdf",
            "__ws_root": str(tmp),
        })

    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = asyncio.run(_run(Path(d)))
    assert out.get("ok") is False
    assert out.get("error_code") == "PDF_REQUIRES_DOCX"


def test_office_pdf_no_converter_unavailable(tmp_path, monkeypatch):
    from project_runtime.office import pdf_render
    from project_runtime.office.providers import OfficePdfProvider

    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK\x03\x04fake")  # 最小伪 zip 头；转换层会再校验

    monkeypatch.setattr(
        pdf_render, "convert_docx_to_pdf",
        lambda *a, **k: {
            "ok": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "error": "no soffice",
        },
    )

    async def _run():
        return await OfficePdfProvider().invoke({
            "path": "out.pdf",
            "source": "a.docx",
            "__ws_root": str(tmp_path),
        })

    out = asyncio.run(_run())
    assert out.get("ok") is False
    assert out.get("error_code") == "RENDERER_UNAVAILABLE"
    assert not (tmp_path / "out.pdf").exists()


def test_pdf_obligation_implies_docx():
    from project_runtime.worker.obligation_plugins import (
        required_plugins_for_tokens,
    )

    need = required_plugins_for_tokens([
        {"id": "t", "exts": [".pdf"]},
    ])
    assert need == {"office_pdf", "office_docx"}
