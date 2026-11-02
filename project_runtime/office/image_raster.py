"""通用示意图：JSON 规格 → Pillow 位图。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_image_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    from PIL import Image, ImageDraw, ImageFont

    width = int(spec.get("width") or 960)
    height = int(spec.get("height") or 540)
    bg = str(spec.get("background") or "#1e293b")
    fg = str(spec.get("color") or "#f8fafc")
    title = str(spec.get("title") or "").strip()
    lines = spec.get("lines") or spec.get("bullets") or []
    if isinstance(lines, str):
        lines = [lines]
    if not isinstance(lines, list):
        lines = []

    img = Image.new("RGB", (max(64, width), max(64, height)), _parse_color(bg))
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("arial.ttf", 36)
        font_body = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    y = 40
    if title:
        draw.text((40, y), title[:80], fill=_parse_color(fg), font=font_title)
        y += 60

    for item in lines[:30]:
        text = str(item if not isinstance(item, dict) else item.get("text") or "")
        draw.text((48, y), f"• {text[:100]}", fill=_parse_color(fg), font=font_body)
        y += 32
        if y > height - 40:
            break

    # 简单框图
    for box in spec.get("boxes") or []:
        if not isinstance(box, dict):
            continue
        x0 = int(box.get("x") or 40)
        y0 = int(box.get("y") or y)
        x1 = x0 + int(box.get("w") or 200)
        y1 = y0 + int(box.get("h") or 80)
        draw.rectangle([x0, y0, x1, y1], outline=_parse_color(fg), width=2)
        label = str(box.get("label") or "")[:40]
        if label:
            draw.text((x0 + 8, y0 + 8), label, fill=_parse_color(fg), font=font_body)

    if not title and not lines and not spec.get("boxes"):
        raise ValueError("spec 至少需要 title、lines/bullets 或 boxes")

    fmt = out_path.suffix.lower().lstrip(".") or "png"
    if fmt == "jpg":
        fmt = "jpeg"
    save_kwargs: dict[str, Any] = {}
    if fmt == "webp":
        save_kwargs["quality"] = 85
    img.save(str(out_path), format=fmt.upper() if fmt != "jpeg" else "JPEG",
             **save_kwargs)
    return {"width": img.width, "height": img.height, "format": fmt}


def _parse_color(value: str) -> tuple[int, int, int]:
    v = (value or "").strip()
    if v.startswith("#") and len(v) == 7:
        return int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)
    return 30, 41, 59
