"""Hermes 式办公/图片：路径合同、拒写、渲染、inspect。"""

from __future__ import annotations

import zipfile

import pytest


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "tasks"
    root.mkdir()
    task = root / "1" / "2"
    task.mkdir(parents=True)
    import project_runtime.worker.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_WORKSPACE_ROOT", root.resolve())
    monkeypatch.chdir(tmp_path)
    return task


@pytest.mark.asyncio
async def test_write_file_denies_pptx(ws):
    from project_runtime.worker.tools import WriteFileProvider

    r = await WriteFileProvider().invoke({
        "path": "fake.pptx",
        "content": "not a pptx",
        "__ws_root": str(ws),
    })
    assert r["ok"] is False
    assert r["error_code"] == "BINARY_SUFFIX_DENIED"
    assert "office_pptx" in (r.get("hint") or "")


@pytest.mark.asyncio
async def test_office_pptx_path_subdir(ws):
    from project_runtime.office.providers import OfficePptxProvider

    r = await OfficePptxProvider().invoke({
        "path": "deliverables/deck.pptx",
        "markdown": "# T\n## A\n- 1\n## B\n- 2\n",
        "__ws_root": str(ws),
    })
    assert r["ok"], r
    abs_path = ws / "deliverables" / "deck.pptx"
    assert abs_path.exists()
    assert zipfile.is_zipfile(abs_path)
    assert r["path"] == "deliverables/deck.pptx"
    assert r["slides"] >= 2


@pytest.mark.asyncio
async def test_office_docx_xlsx_pdf_image_inspect(ws):
    from project_runtime.office.providers import (
        ImageRasterProvider,
        OfficeDocxProvider,
        OfficeInspectProvider,
        OfficePdfProvider,
        OfficeXlsxProvider,
    )

    doc = await OfficeDocxProvider().invoke({
        "path": "out/a.docx",
        "spec": {"title": "报告", "paragraphs": ["第一段", "第二段"]},
        "__ws_root": str(ws),
    })
    assert doc["ok"], doc
    assert zipfile.is_zipfile(ws / "out" / "a.docx")

    xls = await OfficeXlsxProvider().invoke({
        "path": "out/a.xlsx",
        "spec": {"sheets": [{"name": "S1", "data": [["A", "B"], [1, 2]]}]},
        "__ws_root": str(ws),
    })
    assert xls["ok"], xls

    # O2：PDF 须由 docx 转换（无 soffice 时允许 RENDERER_UNAVAILABLE）
    pdf = await OfficePdfProvider().invoke({
        "path": "out/a.pdf",
        "source": "out/a.docx",
        "__ws_root": str(ws),
    })
    if pdf.get("ok"):
        assert (ws / "out" / "a.pdf").read_bytes()[:4] == b"%PDF"
    else:
        assert pdf.get("error_code") in (
            "RENDERER_UNAVAILABLE", "RENDERER_FAILED", "RENDER_TIMEOUT",
        ), pdf

    img = await ImageRasterProvider().invoke({
        "path": "out/a.png",
        "spec": {"title": "示意", "lines": ["步骤一", "步骤二"]},
        "__ws_root": str(ws),
    })
    assert img["ok"], img
    assert (ws / "out" / "a.png").stat().st_size > 100

    insp = await OfficeInspectProvider().invoke({
        "path": "out/a.docx",
        "__ws_root": str(ws),
    })
    assert insp["ok"], insp
    assert insp.get("paragraphs", 0) >= 1


@pytest.mark.asyncio
async def test_arch_png_without_mmdc_fails(ws, monkeypatch):
    from project_runtime.worker import coding_tools
    from project_runtime.worker.coding_tools import ArchDiagramProvider

    async def fake_run(cmd, cwd, timeout=60):
        return {"ok": False, "error": "missing"}

    monkeypatch.setattr(coding_tools, "_run_cmd", fake_run)
    r = await ArchDiagramProvider().invoke({
        "mermaid": "flowchart TD; A-->B",
        "path": "deliverables/arch.png",
        "__ws_root": str(ws),
    })
    assert r["ok"] is False
    assert r.get("error_code") == "RENDERER_UNAVAILABLE"
    assert not (ws / "deliverables" / "arch.png").exists()


@pytest.mark.asyncio
async def test_arch_mmd_ok(ws, monkeypatch):
    from project_runtime.worker.coding_tools import ArchDiagramProvider

    r = await ArchDiagramProvider().invoke({
        "mermaid": "flowchart TD; A-->B",
        "path": "deliverables/arch.mmd",
        "__ws_root": str(ws),
    })
    assert r["ok"], r
    assert (ws / "deliverables" / "arch.mmd").exists()
