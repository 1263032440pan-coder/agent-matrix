"""DOCX 渲染：JSON spec → python-docx。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_docx_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    from docx import Document

    doc = Document()
    title = str(spec.get("title") or "").strip()
    if title:
        doc.add_heading(title, level=0)

    paragraphs = spec.get("paragraphs") or []
    if isinstance(paragraphs, list):
        for p in paragraphs:
            if isinstance(p, dict):
                text = str(p.get("text") or "")
                level = int(p.get("heading") or 0)
                if level > 0:
                    doc.add_heading(text, level=min(level, 4))
                else:
                    doc.add_paragraph(text)
            else:
                doc.add_paragraph(str(p))

    for table in spec.get("tables") or []:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows") or []
        if not rows or not isinstance(rows[0], (list, tuple)):
            continue
        ncols = max(len(r) for r in rows if isinstance(r, (list, tuple)))
        t = doc.add_table(rows=len(rows), cols=ncols)
        for ri, row in enumerate(rows):
            if not isinstance(row, (list, tuple)):
                continue
            for ci, cell in enumerate(row):
                if ci < ncols:
                    t.rows[ri].cells[ci].text = str(cell)

    if not title and not paragraphs and not spec.get("tables"):
        raise ValueError("spec 至少需要 title、paragraphs 或 tables")

    doc.save(str(out_path))
    para_count = len(doc.paragraphs)
    return {"paragraphs": para_count, "tables": len(spec.get("tables") or [])}
