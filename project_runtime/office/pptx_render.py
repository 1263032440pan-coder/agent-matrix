"""PPTX 渲染：markdown 或 JSON spec → python-pptx（O3 加厚）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def markdown_to_pptx_spec(markdown: str) -> dict[str, Any]:
    """markdown → OfficeSpec v1（走同一加厚渲染，避免 V0 白版占位符）。"""
    deck_title = ""
    slides: list[dict[str, Any]] = []
    current_title: str | None = None
    body_lines: list[str] = []
    in_fence = False
    h2_count = 0

    def flush(*, section: bool = False) -> None:
        nonlocal current_title, body_lines
        if current_title is None and not body_lines:
            return
        bullets: list[dict[str, Any]] = []
        for line in body_lines[:24]:
            text = line
            level = 0
            if text.startswith(("- ", "* ")):
                text = text[2:]
            elif text.startswith("  - ") or text.startswith("  * "):
                text = text[4:]
                level = 1
            bullets.append({"text": text[:500], "level": level})
        title = (current_title or "内容").strip() or "内容"
        slides.append({
            "layout": "section" if section and not bullets else "bullets",
            "title": title,
            "bullets": bullets,
        })
        body_lines = []
        current_title = None

    for raw in (markdown or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if current_title is not None or body_lines:
                body_lines.append(stripped[:200])
            continue
        if in_fence:
            if current_title is not None or body_lines or deck_title:
                body_lines.append(stripped[:500] if stripped else "")
            continue
        if stripped == "---":
            flush()
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            flush()
            deck_title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            flush()
            h2_count += 1
            current_title = stripped[3:].strip()
            continue
        if not stripped:
            continue
        if current_title is None and not deck_title:
            current_title = "内容"
            h2_count += 1
        if current_title is None and deck_title:
            current_title = ""
            h2_count += 1
        body_lines.append(stripped[:500])

    flush()
    if not slides and not deck_title:
        deck_title = "演示文稿"
    return {
        "spec_version": "1",
        "theme": {"preset": "business-blue"},
        "title": deck_title or "演示文稿",
        "slides": slides or [{"layout": "bullets", "title": "内容", "bullets": []}],
        "_h2_count": h2_count,
    }


def render_pptx_from_markdown(markdown: str, out_path: Path) -> dict[str, Any]:
    """markdown → OfficeSpec → 加厚渲染（与 spec 同路径）。"""
    spec = markdown_to_pptx_spec(markdown)
    h2 = int(spec.pop("_h2_count", 0) or 0)
    meta = render_pptx_from_spec(spec, out_path)
    meta["h2_count"] = h2
    return meta


def _rgb(hex_color: str):
    from pptx.dml.color import RGBColor

    h = (hex_color or "1F4E79").lstrip("#")
    if len(h) != 6:
        h = "1F4E79"
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _fill_slide_bg(slide, color_hex: str) -> None:
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    try:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(color_hex)
    except Exception:  # noqa: BLE001 — 部分布局只读背景，改用全幅矩形
        try:
            shape = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, Inches(0), Inches(0),
                Inches(10), Inches(7.5),
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb(color_hex)
            shape.line.fill.background()
            # 移到最底层
            sp_tree = slide.shapes._spTree  # noqa: SLF001
            sp = shape._element  # noqa: SLF001
            sp_tree.remove(sp)
            sp_tree.insert(2, sp)
        except Exception:  # noqa: BLE001
            logger.debug("bg fill failed", exc_info=True)


def _add_accent_bar(slide, color_hex: str) -> None:
    """左侧色条——避免默认占位符「白底无样式」观感。"""
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches

    try:
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0), Inches(0),
            Inches(0.18), Inches(7.5),
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(color_hex)
        bar.line.fill.background()
    except Exception:  # noqa: BLE001
        logger.debug("accent bar failed", exc_info=True)


def _apply_font(paragraph, *, name: str | None, size_pt: float | None = None,
                bold: bool | None = None, color_hex: str | None = None) -> None:
    from pptx.util import Pt

    if name:
        paragraph.font.name = name
    if size_pt is not None:
        paragraph.font.size = Pt(size_pt)
    if bold is not None:
        paragraph.font.bold = bold
    if color_hex:
        paragraph.font.color.rgb = _rgb(color_hex)


def _resolve_image(
    path_str: str, assets_root: Path | None, degraded: list[str],
) -> Path | None:
    if not path_str:
        return None
    p = Path(path_str)
    if not p.is_absolute() and assets_root is not None:
        p = (assets_root / path_str).resolve()
    if not p.is_file():
        degraded.append("image_missing")
        return None
    if p.suffix.lower() == ".svg":
        try:
            import cairosvg  # type: ignore

            png = p.with_suffix(".png")
            cairosvg.svg2png(url=str(p), write_to=str(png))
            return png
        except Exception:  # noqa: BLE001
            degraded.append("svg")
            return None
    return p


def render_pptx_from_spec(
    spec: dict,
    out_path: Path,
    *,
    assets_root: Path | None = None,
) -> dict[str, Any]:
    """JSON OfficeSpec（经 normalize）→ pptx。"""
    from pptx import Presentation
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches

    from project_runtime.office.spec import normalize_pptx_spec

    norm, degraded = normalize_pptx_spec(spec)
    theme = norm["theme"]
    primary = theme.get("primary") or "1F4E79"
    accent = theme.get("accent") or primary
    bg_default = theme.get("background") or "FFFFFF"
    font_h = theme.get("font_heading") or "Microsoft YaHei"
    font_b = theme.get("font_body") or "Microsoft YaHei"

    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    deck_title = str(norm.get("title") or "").strip()
    slides = norm["slides"]
    blank = prs.slide_layouts[6]

    if deck_title:
        cover = prs.slides.add_slide(blank)
        _fill_slide_bg(cover, primary)
        box = cover.shapes.add_textbox(
            Inches(0.8), Inches(2.6), Inches(8.4), Inches(1.8),
        )
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = deck_title
        _apply_font(p, name=font_h, size_pt=36, bold=True, color_hex="FFFFFF")
        p.alignment = PP_ALIGN.CENTER
        sub = cover.shapes.add_textbox(
            Inches(0.8), Inches(4.6), Inches(8.4), Inches(0.5),
        )
        sp = sub.text_frame.paragraphs[0]
        sp.text = "Project Autonomous Agent"
        _apply_font(sp, name=font_b, size_pt=14, color_hex="D6E3F0")
        sp.alignment = PP_ALIGN.CENTER

    for item in slides:
        layout = item.get("layout") or "bullets"
        title = str(item.get("title") or "").strip() or " "
        bg = item.get("background") or {}
        bg_color = str(
            (bg.get("color") if isinstance(bg, dict) else None) or bg_default
        )
        bullets = item.get("bullets") or []
        body = str(item.get("body") or "").strip()

        if layout in ("title", "section", "closing"):
            slide = prs.slides.add_slide(blank)
            _fill_slide_bg(
                slide, primary if layout != "closing" else bg_color,
            )
            box = slide.shapes.add_textbox(
                Inches(0.8), Inches(3.0), Inches(8.4), Inches(1.2),
            )
            p = box.text_frame.paragraphs[0]
            p.text = title
            _apply_font(
                p, name=font_h,
                size_pt=32 if layout != "section" else 28,
                bold=True,
                color_hex="FFFFFF" if layout != "closing" else primary,
            )
            p.alignment = PP_ALIGN.CENTER
            if layout == "closing" and bullets:
                sub = slide.shapes.add_textbox(
                    Inches(1.5), Inches(4.4), Inches(7), Inches(1),
                )
                tf = sub.text_frame
                for i, b in enumerate(bullets[:6]):
                    pp = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    pp.text = b.get("text") if isinstance(b, dict) else str(b)
                    _apply_font(pp, name=font_b, size_pt=16, color_hex=primary)
                    pp.alignment = PP_ALIGN.CENTER
            continue

        # 内容页一律 blank + 色条，避免 PowerPoint 默认占位符「无样式」
        slide = prs.slides.add_slide(blank)
        _fill_slide_bg(slide, bg_color)
        _add_accent_bar(slide, accent if layout != "image_full" else primary)

        tbox = slide.shapes.add_textbox(
            Inches(0.55), Inches(0.28), Inches(9), Inches(0.7),
        )
        tp = tbox.text_frame.paragraphs[0]
        tp.text = title
        _apply_font(tp, name=font_h, size_pt=26, bold=True, color_hex=primary)

        if layout == "two_column":
            left = bullets[: max(1, len(bullets) // 2)]
            right = bullets[len(left):]
            for items_col, left_x in ((left, 0.55), (right, 5.2)):
                box = slide.shapes.add_textbox(
                    Inches(left_x), Inches(1.15), Inches(4.2), Inches(5.5),
                )
                tf = box.text_frame
                tf.word_wrap = True
                for i, b in enumerate(items_col[:16]):
                    pp = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    if isinstance(b, dict):
                        pp.text = b.get("text") or ""
                        pp.level = int(b.get("level") or 0)
                    else:
                        pp.text = str(b)
                    _apply_font(pp, name=font_b, size_pt=16)
        elif layout == "image_full":
            img = item.get("image") if isinstance(item.get("image"), dict) else {}
            resolved = _resolve_image(
                str(img.get("path") or ""), assets_root, degraded,
            )
            if resolved:
                slide.shapes.add_picture(
                    str(resolved), Inches(0.8), Inches(1.2),
                    width=Inches(8.4),
                )
        elif layout == "image_left":
            img = item.get("image") if isinstance(item.get("image"), dict) else {}
            resolved = _resolve_image(
                str(img.get("path") or ""), assets_root, degraded,
            )
            if resolved:
                slide.shapes.add_picture(
                    str(resolved), Inches(0.5), Inches(1.15),
                    width=Inches(4.2),
                )
            box = slide.shapes.add_textbox(
                Inches(5.0), Inches(1.15), Inches(4.5), Inches(5.5),
            )
            tf = box.text_frame
            tf.word_wrap = True
            for i, b in enumerate(bullets[:16]):
                pp = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                if isinstance(b, dict):
                    pp.text = b.get("text") or ""
                    pp.level = int(b.get("level") or 0)
                else:
                    pp.text = str(b)
                _apply_font(pp, name=font_b, size_pt=16)
        elif layout == "table":
            table_spec = item.get("table") if isinstance(item.get("table"), dict) else {}
            headers = table_spec.get("headers") or []
            rows = table_spec.get("rows") or []
            data = [headers] + list(rows) if headers else list(rows)
            if data and isinstance(data[0], (list, tuple)):
                ncols = max(len(r) for r in data)
                nrows = len(data)
                shape = slide.shapes.add_table(
                    nrows, ncols, Inches(0.55), Inches(1.2),
                    Inches(9), Inches(min(5.5, 0.4 * nrows + 0.5)),
                )
                tbl = shape.table
                for ri, row in enumerate(data):
                    for ci in range(ncols):
                        val = row[ci] if ci < len(row) else ""
                        cell = tbl.cell(ri, ci)
                        cell.text = str(val)
                        for p in cell.text_frame.paragraphs:
                            _apply_font(
                                p, name=font_b, size_pt=12,
                                bold=(ri == 0 and bool(headers)),
                                color_hex=primary if ri == 0 and headers else None,
                            )
        else:
            lines: list[Any] = list(bullets)
            if body:
                lines.append({"text": body, "level": 0})
            box = slide.shapes.add_textbox(
                Inches(0.55), Inches(1.15), Inches(9), Inches(5.6),
            )
            tf = box.text_frame
            tf.word_wrap = True
            for i, line in enumerate(lines[:24]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                if isinstance(line, dict):
                    p.text = str(line.get("text") or "")[:500]
                    p.level = int(line.get("level") or 0)
                else:
                    text = str(line)
                    if text.startswith("- "):
                        text = text[2:]
                    p.text = text[:500]
                _apply_font(p, name=font_b, size_pt=16)

        notes = str(item.get("notes") or "").strip()
        if notes:
            try:
                slide.notes_slide.notes_text_frame.text = notes[:2000]
            except Exception:  # noqa: BLE001
                pass

    prs.save(str(out_path))
    meta = _pptx_meta(out_path) | {"h2_count": len(slides)}
    if degraded:
        meta["degraded_features"] = sorted(set(degraded))
    return meta


def _pptx_meta(out_path: Path) -> dict[str, Any]:
    from pptx import Presentation as _Pres

    m = _Pres(str(out_path))
    slides = list(m.slides)
    non_empty = sum(
        1 for sl in slides
        if any(sh.has_text_frame and sh.text_frame.text.strip() for sh in sl.shapes)
    )
    has_title = bool(
        slides and any(
            sh.has_text_frame and sh.text_frame.text.strip()
            for sh in slides[0].shapes
        )
    )
    return {
        "slides": len(slides),
        "non_empty_slides": non_empty,
        "has_title_text": has_title,
    }


def load_spec(payload: dict, ws_root: Path | None) -> dict | str | None:
    """返回 dict(spec) / str(markdown) / None。"""
    if payload.get("spec") is not None:
        raw = payload["spec"]
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            return json.loads(raw)
    spec_path = str(payload.get("spec_path") or "").strip()
    if spec_path and ws_root is not None:
        from project_runtime.worker.tools import _within, resolve_task_path

        p = resolve_task_path(spec_path, ws_root)
        if not _within(p, ws_root):
            raise ValueError(f"spec_path 越界: {spec_path}")
        return json.loads(p.read_text(encoding="utf-8"))
    if spec_path:
        p = Path(spec_path)
        return json.loads(p.read_text(encoding="utf-8"))
    md = str(payload.get("markdown") or "")
    if md.strip():
        return md
    return None
