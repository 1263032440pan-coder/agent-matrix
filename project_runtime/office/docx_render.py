"""DOCX 渲染：JSON OfficeSpec → python-docx（O3 加厚）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_docx_from_spec(
    spec: dict,
    out_path: Path,
    *,
    assets_root: Path | None = None,
) -> dict[str, Any]:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    from project_runtime.office.spec import normalize_docx_spec

    norm, degraded_in = normalize_docx_spec(spec)
    degraded: list[str] = list(degraded_in)
    theme = norm.get("theme") or {}
    primary = (theme.get("primary") or "1F4E79").lstrip("#")

    doc = Document()
    # 正文字体
    style = doc.styles["Normal"]
    style.font.name = theme.get("font_body") or "Microsoft YaHei"
    style.font.size = Pt(11)
    try:
        style.element.rPr.rFonts.set(qn("w:eastAsia"), style.font.name)
    except Exception:  # noqa: BLE001
        pass

    cover = norm.get("cover")
    if isinstance(cover, dict):
        t = str(cover.get("title") or norm.get("title") or "").strip()
        if t:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(t)
            run.bold = True
            run.font.size = Pt(22)
            try:
                run.font.color.rgb = RGBColor(
                    int(primary[0:2], 16),
                    int(primary[2:4], 16),
                    int(primary[4:6], 16),
                )
            except Exception:  # noqa: BLE001
                pass
        sub = str(cover.get("subtitle") or "").strip()
        if sub:
            p = doc.add_paragraph(sub)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        date = str(cover.get("date") or "").strip()
        if date:
            p = doc.add_paragraph(date)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_page_break()

    header_cfg = norm.get("header") if isinstance(norm.get("header"), dict) else {}
    footer_cfg = norm.get("footer") if isinstance(norm.get("footer"), dict) else {}
    if header_cfg.get("text"):
        section = doc.sections[0]
        hp = section.header.paragraphs[0]
        hp.text = str(header_cfg["text"])
    if footer_cfg.get("text") or footer_cfg.get("page_number"):
        section = doc.sections[0]
        fp = section.footer.paragraphs[0]
        bits = []
        if footer_cfg.get("text"):
            bits.append(str(footer_cfg["text"]))
        if footer_cfg.get("page_number"):
            bits.append("第 %PAGE% 页")  # 占位提示；真 PAGE 域需复杂 oxml
        fp.text = " · ".join(bits) if bits else ""

    heading_count = 0
    para_count = 0
    list_count = 0
    for block in norm.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "paragraph")
        if btype == "heading":
            level = max(1, min(3, int(block.get("level") or 1)))
            doc.add_heading(str(block.get("text") or ""), level=level)
            heading_count += 1
        elif btype == "page_break":
            doc.add_page_break()
        elif btype == "list":
            items = block.get("items") or []
            style_name = (
                "List Number" if block.get("ordered") else "List Bullet"
            )
            for it in items:
                text = it.get("text") if isinstance(it, dict) else str(it)
                level = int(
                    (it.get("level") if isinstance(it, dict) else None)
                    or block.get("level")
                    or 0
                )
                try:
                    p = doc.add_paragraph(str(text), style=style_name)
                except Exception:  # noqa: BLE001 — 部分环境无列表样式
                    p = doc.add_paragraph(
                        ("• " if not block.get("ordered") else "") + str(text)
                    )
                if level:
                    p.paragraph_format.left_indent = Cm(0.75 * level)
                list_count += 1
        elif btype == "table":
            rows = block.get("rows") or []
            if rows and isinstance(rows[0], (list, tuple)):
                ncols = max(len(r) for r in rows if isinstance(r, (list, tuple)))
                t = doc.add_table(rows=len(rows), cols=ncols)
                t.style = "Table Grid"
                for ri, row in enumerate(rows):
                    if not isinstance(row, (list, tuple)):
                        continue
                    for ci, cell in enumerate(row):
                        if ci < ncols:
                            t.rows[ri].cells[ci].text = str(cell)
        elif btype == "image":
            path = str(block.get("path") or "")
            if path and assets_root is not None:
                img = (assets_root / path).resolve()
                if img.is_file() and img.suffix.lower() != ".svg":
                    try:
                        doc.add_picture(str(img), width=Cm(12))
                    except Exception:  # noqa: BLE001
                        degraded.append("image")
                elif img.suffix.lower() == ".svg":
                    degraded.append("svg")
                else:
                    degraded.append("image_missing")
        else:
            text = str(block.get("text") or "")
            p = doc.add_paragraph(text)
            indent = block.get("indent")
            if indent is not None:
                try:
                    p.paragraph_format.first_line_indent = Cm(float(indent))
                except (TypeError, ValueError):
                    p.paragraph_format.first_line_indent = Cm(0.74)
            spacing = block.get("spacing")
            if spacing is not None:
                try:
                    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
                    p.paragraph_format.line_spacing = float(spacing)
                except (TypeError, ValueError):
                    pass
            para_count += 1

    if not (norm.get("blocks") or cover):
        raise ValueError("spec 至少需要 title、paragraphs、tables 或 blocks")

    doc.save(str(out_path))
    meta: dict[str, Any] = {
        "paragraphs": len(doc.paragraphs),
        "headings": heading_count,
        "lists": list_count,
        "tables": sum(
            1 for b in (norm.get("blocks") or [])
            if isinstance(b, dict) and b.get("type") == "table"
        ),
    }
    if degraded:
        meta["degraded_features"] = sorted(set(degraded))
    return meta
