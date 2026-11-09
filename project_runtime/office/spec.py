"""OfficeSpec v1 规范化（方案 §三）— V0 薄 spec 兼容。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

THEME_PRESETS: dict[str, dict[str, str]] = {
    "business-blue": {
        "preset": "business-blue",
        "primary": "1F4E79",
        "accent": "2E75B6",
        "background": "FFFFFF",
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
    "clean-gray": {
        "preset": "clean-gray",
        "primary": "404040",
        "accent": "707070",
        "background": "F5F5F5",
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
    "dark": {
        "preset": "dark",
        "primary": "E8E8E8",
        "accent": "5B9BD5",
        "background": "1E1E1E",
        "font_heading": "Microsoft YaHei",
        "font_body": "Microsoft YaHei",
    },
}

_PPT_LAYOUTS = frozenset({
    "title", "section", "bullets", "two_column",
    "image_left", "image_full", "table", "closing",
})


def _resolve_theme(raw: dict | None) -> dict[str, str]:
    theme = dict(THEME_PRESETS["business-blue"])
    if not isinstance(raw, dict):
        return theme
    preset = str(raw.get("preset") or "").strip()
    if preset in THEME_PRESETS:
        theme = dict(THEME_PRESETS[preset])
    for k in ("primary", "accent", "background", "font_heading", "font_body"):
        if raw.get(k):
            theme[k] = str(raw[k]).lstrip("#")
    return theme


def normalize_pptx_spec(spec: dict) -> tuple[dict[str, Any], list[str]]:
    """→ (v1 spec, degraded_features)。"""
    degraded: list[str] = []
    out = deepcopy(spec) if isinstance(spec, dict) else {}
    out["spec_version"] = "1"
    out["theme"] = _resolve_theme(out.get("theme") if isinstance(out.get("theme"), dict) else None)

    slides = out.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("spec.slides 必须为非空数组")

    norm_slides: list[dict] = []
    for i, item in enumerate(slides):
        if not isinstance(item, dict):
            continue
        s = dict(item)
        if s.get("chart") is not None:
            degraded.append("chart")
            s.pop("chart", None)
        if s.get("animation") is not None:
            degraded.append("animation")
            s.pop("animation", None)
        layout = str(s.get("layout") or "").strip() or "bullets"
        if layout not in _PPT_LAYOUTS:
            layout = "bullets"
        # 有图无 layout 时偏向 image_left
        if "image" in s and str(item.get("layout") or "") == "":
            layout = "image_left"
        if "table" in s and str(item.get("layout") or "") == "":
            layout = "table"
        s["layout"] = layout
        # 嵌套 bullets 归一为 {text, level}
        bullets = s.get("bullets")
        if isinstance(bullets, list):
            nb = []
            for b in bullets:
                if isinstance(b, dict):
                    nb.append({
                        "text": str(b.get("text") or "")[:500],
                        "level": max(0, min(2, int(b.get("level") or 0))),
                    })
                else:
                    nb.append({"text": str(b)[:500], "level": 0})
            s["bullets"] = nb
        # 背景
        bg = s.get("background")
        if bg is None and out["theme"].get("background"):
            s["background"] = {"color": out["theme"]["background"]}
        elif isinstance(bg, str):
            s["background"] = {"color": bg.lstrip("#")}
        norm_slides.append(s)
    if not norm_slides:
        raise ValueError("spec.slides 无有效页")
    out["slides"] = norm_slides
    # 去重 degraded
    degraded = sorted(set(degraded))
    return out, degraded


def normalize_docx_spec(spec: dict) -> tuple[dict[str, Any], list[str]]:
    degraded: list[str] = []
    out = deepcopy(spec) if isinstance(spec, dict) else {}
    out["spec_version"] = "1"
    out["theme"] = _resolve_theme(
        out.get("theme") if isinstance(out.get("theme"), dict) else None
    )

    if isinstance(out.get("blocks"), list) and out["blocks"]:
        blocks = []
        for b in out["blocks"]:
            if not isinstance(b, dict):
                continue
            b = dict(b)
            if b.get("type") == "toc":
                degraded.append("toc")
                continue  # 内置不生成 TOC
            blocks.append(b)
        out["blocks"] = blocks
        return out, sorted(set(degraded))

    # V0 → blocks
    blocks: list[dict] = []
    title = str(out.get("title") or "").strip()
    if title:
        blocks.append({"type": "heading", "level": 1, "text": title})
    for p in out.get("paragraphs") or []:
        if isinstance(p, dict):
            text = str(p.get("text") or "")
            level = int(p.get("heading") or 0)
            if level > 0:
                blocks.append({
                    "type": "heading",
                    "level": min(max(level, 1), 3),
                    "text": text,
                })
            else:
                blocks.append({
                    "type": "paragraph",
                    "text": text,
                    "indent": p.get("indent"),
                    "spacing": p.get("spacing"),
                    "style": p.get("style"),
                })
        else:
            blocks.append({"type": "paragraph", "text": str(p)})
    for table in out.get("tables") or []:
        if isinstance(table, dict):
            blocks.append({"type": "table", **table})
    if not blocks:
        raise ValueError("spec 至少需要 title、paragraphs、tables 或 blocks")
    out["blocks"] = blocks
    return out, sorted(set(degraded))


def normalize_xlsx_spec(spec: dict) -> tuple[dict[str, Any], list[str]]:
    degraded: list[str] = []
    out = deepcopy(spec) if isinstance(spec, dict) else {}
    out["spec_version"] = "1"
    sheets = out.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise ValueError("spec.sheets 必须为非空数组")
    for sh in sheets:
        if not isinstance(sh, dict):
            continue
        if sh.get("chart") is not None or sh.get("pivot") is not None:
            degraded.append("chart" if sh.get("chart") is not None else "pivot")
            sh.pop("chart", None)
            sh.pop("pivot", None)
    return out, sorted(set(degraded))
