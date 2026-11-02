"""PDF 渲染：JSON spec → reportlab。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_pdf_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    # 尽量用系统中文字体；失败则 Helvetica（中文会缺字但文件合法）
    font_name = "Helvetica"
    for candidate in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ):
        p = Path(candidate)
        if p.exists():
            try:
                pdfmetrics.registerFont(TTFont("OfficeCJK", str(p), subfontIndex=0))
                font_name = "OfficeCJK"
                break
            except Exception:  # noqa: BLE001
                continue

    title = str(spec.get("title") or "Document").strip()
    paragraphs = spec.get("paragraphs") or []
    if not isinstance(paragraphs, list):
        paragraphs = [str(paragraphs)]

    c = canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4
    y = height - 72
    c.setFont(font_name, 16)
    c.drawString(72, y, title[:80])
    y -= 36
    c.setFont(font_name, 11)
    lines_written = 0
    for para in paragraphs:
        text = str(para if not isinstance(para, dict) else para.get("text") or "")
        for line in _wrap(text, 42):
            if y < 72:
                c.showPage()
                c.setFont(font_name, 11)
                y = height - 72
            c.drawString(72, y, line)
            y -= 16
            lines_written += 1
    if lines_written == 0 and not title:
        raise ValueError("spec 至少需要 title 或 paragraphs")
    c.save()
    return {"pages": c.getPageNumber(), "lines": lines_written}


def _wrap(text: str, width: int) -> list[str]:
    text = text.replace("\r", "")
    out: list[str] = []
    for para in text.split("\n"):
        s = para.strip()
        if not s:
            out.append("")
            continue
        while len(s) > width:
            out.append(s[:width])
            s = s[width:]
        out.append(s)
    return out or [""]


def try_soffice_preview(src: Path) -> dict[str, Any]:
    """可选 LibreOffice 转 PDF/预览；缺失则跳过，不写占位文件。"""
    import shutil
    import subprocess

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return {
            "preview": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "note": "未安装 LibreOffice/soffice——跳过预览",
        }
    out_dir = src.parent
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(src)],
            check=False, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {
            "preview": False,
            "error_code": "RENDERER_FAILED",
            "note": f"soffice 预览失败: {e}",
        }
    pdf = out_dir / (src.stem + ".pdf")
    if pdf.exists():
        return {"preview": True, "preview_path": str(pdf)}
    return {
        "preview": False,
        "error_code": "RENDERER_FAILED",
        "note": "soffice 未产出 pdf",
    }
