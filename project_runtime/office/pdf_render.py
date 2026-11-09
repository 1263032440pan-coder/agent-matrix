"""PDF：docx→pdf 转换（主路径）；reportlab spec 渲染已弃用。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Windows 默认安装路径常未进 PATH；health/which 会漏检
_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


def find_soffice() -> str | None:
    """定位 LibreOffice CLI；PATH 优先，否则扫常见安装路径。"""
    for name in ("soffice", "libreoffice"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in _SOFFICE_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _soffice_user_installation() -> str:
    """独立 UserInstallation，避免本机默认配置损坏导致 soffice 挂死。"""
    root = Path(tempfile.gettempdir()) / "agent-matrix-lo-profile"
    root.mkdir(parents=True, exist_ok=True)
    return "file:///" + root.resolve().as_posix()


def convert_docx_to_pdf(
    source: Path,
    out_path: Path,
    *,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """LibreOffice 将 docx 转为 pdf，写入 out_path。

    成功: {"ok": True, "pages": optional}
    失败: {"ok": False, "error_code": ..., "error": ...}
    """
    soffice = find_soffice()
    if not soffice:
        return {
            "ok": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "error": "未安装 LibreOffice/soffice——无法将 Word 转为 PDF",
        }
    if not source.is_file():
        return {
            "ok": False,
            "error_code": "NOT_FOUND",
            "error": f"源文件不存在: {source}",
        }
    # OOXML 最小魔数
    try:
        head = source.read_bytes()[:4]
    except OSError as e:
        return {
            "ok": False,
            "error_code": "INVALID_SOURCE",
            "error": f"无法读取源文件: {e}",
        }
    if head != b"PK\x03\x04":
        return {
            "ok": False,
            "error_code": "INVALID_SOURCE",
            "error": "源文件不是合法 OOXML（ZIP）docx",
        }

    out_path = Path(out_path)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    # soffice 在 outdir 写出 source.stem.pdf，再原子改名到 out_path
    try:
        proc = subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--nologo",
                "--nodefault",
                f"-env:UserInstallation={_soffice_user_installation()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(source),
            ],
            check=False,
            capture_output=True,
            timeout=timeout_s,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_code": "RENDER_TIMEOUT",
            "error": f"soffice 转换超时（>{timeout_s}s）",
        }
    except OSError as e:
        return {
            "ok": False,
            "error_code": "RENDERER_FAILED",
            "error": f"soffice 启动失败: {e}",
        }

    produced = out_dir / (source.stem + ".pdf")
    if not produced.is_file():
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[:300]
        return {
            "ok": False,
            "error_code": "RENDERER_FAILED",
            "error": err or "soffice 未产出 pdf",
        }
    if produced.resolve() != out_path.resolve():
        try:
            if out_path.exists():
                out_path.unlink()
            produced.replace(out_path)
        except OSError as e:
            return {
                "ok": False,
                "error_code": "RENDERER_FAILED",
                "error": f"无法写入目标 pdf: {e}",
            }
    return {"ok": True, "backend": "soffice"}


def render_pdf_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    """已弃用：勿作主路径。保留供过渡期测试；新代码请用 convert_docx_to_pdf。"""
    logger.warning("render_pdf_from_spec is deprecated; use convert_docx_to_pdf")
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
    soffice = find_soffice()
    if not soffice:
        return {
            "preview": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "note": "未安装 LibreOffice/soffice——跳过预览",
        }
    out_dir = src.parent
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--nologo",
                "--nodefault",
                f"-env:UserInstallation={_soffice_user_installation()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(src),
            ],
            check=False,
            capture_output=True,
            timeout=120,
            env={**os.environ},
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
