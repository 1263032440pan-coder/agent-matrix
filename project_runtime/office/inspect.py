"""office_inspect：按后缀读回结构 JSON。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any


def inspect_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": f"文件不存在: {path}"}
    suf = path.suffix.lower()
    try:
        if suf == ".pptx":
            return {"ok": True, "kind": "pptx", **_inspect_pptx(path)}
        if suf == ".docx":
            return {"ok": True, "kind": "docx", **_inspect_docx(path)}
        if suf == ".xlsx":
            return {"ok": True, "kind": "xlsx", **_inspect_xlsx(path)}
        if suf == ".pdf":
            return {"ok": True, "kind": "pdf", **_inspect_pdf(path)}
        if suf in (".png", ".jpg", ".jpeg", ".webp"):
            return {"ok": True, "kind": "image", **_inspect_image(path)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"inspect 失败: {e}", "kind": suf.lstrip(".")}
    return {"ok": False, "error": f"不支持的后缀: {suf}"}


def _inspect_pptx(path: Path) -> dict[str, Any]:
    from pptx import Presentation

    if not zipfile.is_zipfile(path):
        return {"valid_ooxml": False, "slides": 0}
    prs = Presentation(str(path))
    slides = list(prs.slides)
    return {
        "valid_ooxml": True,
        "slides": len(slides),
        "non_empty_slides": sum(
            1 for sl in slides
            if any(sh.has_text_frame and sh.text_frame.text.strip()
                   for sh in sl.shapes)
        ),
    }


def _inspect_docx(path: Path) -> dict[str, Any]:
    from docx import Document

    if not zipfile.is_zipfile(path):
        return {"valid_ooxml": False, "paragraphs": 0}
    doc = Document(str(path))
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return {
        "valid_ooxml": True,
        "paragraphs": len(texts),
        "tables": len(doc.tables),
        "preview": texts[:5],
    }


def _inspect_xlsx(path: Path) -> dict[str, Any]:
    from openpyxl import load_workbook

    if not zipfile.is_zipfile(path):
        return {"valid_ooxml": False, "sheets": []}
    wb = load_workbook(str(path), read_only=True, data_only=False)
    names = list(wb.sheetnames)
    wb.close()
    return {"valid_ooxml": True, "sheets": names, "sheet_count": len(names)}


def _inspect_pdf(path: Path) -> dict[str, Any]:
    data = path.read_bytes()[:8]
    return {
        "valid_pdf": data.startswith(b"%PDF"),
        "size": path.stat().st_size,
    }


def _inspect_image(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as im:
        return {"width": im.width, "height": im.height, "format": im.format}
