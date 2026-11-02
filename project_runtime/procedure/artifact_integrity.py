"""假/损坏产物内容完整性校验（Hermes 工具名 nudge + 软放行前置）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

# 占位模板（整文件匹配，大小写不敏感）
_PLACEHOLDER_EXACT = frozenset({
    "this is a placeholder.",
    "todo: generate",
    "假文件",
    "占位符",
    "占位",
})

_OOXML_PPTX = (".pptx",)
_OOXML_DOCX = (".docx",)
_OOXML_XLSX = (".xlsx",)
_BITMAP = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_PDF = (".pdf",)
_ZIP = (".zip",)
_TEXT_DOC = (".md", ".txt", ".rst", ".adoc")
_CODE = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs",
    ".toml", ".json", ".html", ".css",
)
_MMD = (".mmd",)

_BINARY_CLAIM = _OOXML_PPTX + _OOXML_DOCX + _OOXML_XLSX + _BITMAP + _PDF + _ZIP


def collect_scan_rel_paths(root: Path) -> list[str]:
    """扫描工作区相对路径（用于 Stop 后完整性检查）。"""
    if not root.exists():
        return []
    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                out.append(p.relative_to(root).as_posix())
            except ValueError:
                continue
    return out


def check_paths_integrity(
    root: Path, rel_paths: list[str],
) -> list[dict[str, Any]]:
    """返回失败列表 [{path, kind, detail}]；空=通过。"""
    failures: list[dict[str, Any]] = []
    for rel in rel_paths:
        rel_n = str(rel).replace("\\", "/").lstrip("./")
        path = root / rel_n
        if not path.is_file():
            continue
        kind, detail = _check_one(path)
        if kind:
            failures.append({
                "path": rel_n,
                "kind": kind,
                "detail": detail,
            })
    return failures


def integrity_repair_nudge(
    used: int,
    failures: list[dict[str, Any]],
    *,
    max_repairs: int = 3,
) -> str:
    """H4：文案对齐 Hermes 式工具名（office_* / image_raster / arch_diagram）。"""
    lines = [
        "【交付物内容损坏/不合法—请返工】下列文件扩展名已匹配但内容校验失败，",
        "禁止再次用 write_file 写入假后缀或明文占位：",
    ]
    for f in failures[:12]:
        lines.append(f"- {f.get('path')}: {f.get('detail') or f.get('kind')}")
    lines.extend([
        "修复要求（按类型）：",
        "- .pptx → tool:office_pptx（JSON spec 或 markdown）生成真实 OOXML；",
        "- .docx → tool:office_docx；.xlsx → tool:office_xlsx；.pdf → tool:office_pdf；",
        "- .png/.jpg/.webp → tool:image_raster 或 tool:arch_diagram（mermaid）；",
        "- .md/.txt 等文档 → 写入真实正文（非占位句）；",
        "- 源码/.json → 写入可解析、非空的真实内容；",
        "请修复后继续；收工前会再次做内容完整性校验。",
        f"（完整性返工 {used + 1}/{max_repairs}）",
    ])
    return "\n".join(lines)


def _check_one(path: Path) -> tuple[str | None, str]:
    suf = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return "unreadable", "无法读取文件"

    if suf in _OOXML_PPTX:
        return _check_ooxml(path, size, need=("ppt/",), kind_prefix="pptx")
    if suf in _OOXML_DOCX:
        return _check_ooxml(path, size, need=("word/",), kind_prefix="docx")
    if suf in _OOXML_XLSX:
        return _check_ooxml(path, size, need=("xl/",), kind_prefix="xlsx")
    if suf in _BITMAP:
        return _check_bitmap(path, size, suf)
    if suf in _PDF:
        return _check_pdf(path, size)
    if suf in _ZIP:
        return _check_zip_only(path, size)
    if suf in _MMD:
        return _check_mmd(path, size)
    if suf in _TEXT_DOC:
        return _check_text_doc(path, size)
    if suf in _CODE:
        return _check_code(path, size, suf)
    if suf in _BINARY_CLAIM:
        return _check_binary_claim(path, size)
    # 未知后缀：仅空文件
    if size == 0:
        return "empty", "空文件"
    return None, ""


def _check_ooxml(
    path: Path, size: int, *, need: tuple[str, ...], kind_prefix: str,
) -> tuple[str | None, str]:
    if size < 2048:
        if _looks_plaintext_header(path):
            return (
                "plaintext_disguised_as_binary",
                f"明文冒充 {path.suffix}（体积 {size}B）",
            )
        return f"{kind_prefix}_too_small", f"体积过小（{size}B），非合法 OOXML"
    if _looks_plaintext_header(path):
        return (
            "plaintext_disguised_as_binary",
            f"明文冒充 {path.suffix}",
        )
    if not zipfile.is_zipfile(path):
        return f"{kind_prefix}_not_ooxml", "不是合法 ZIP/OOXML"
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not any(
                n == "[Content_Types].xml" or n.endswith("/[Content_Types].xml")
                for n in names
            ):
                return f"{kind_prefix}_not_ooxml", "缺少 [Content_Types].xml"
            if not any(any(n.startswith(p) for p in need) for n in names):
                return f"{kind_prefix}_not_ooxml", f"缺少 {need[0]} 部件"
    except zipfile.BadZipFile:
        return f"{kind_prefix}_not_ooxml", "ZIP 损坏"
    return None, ""


def _check_bitmap(path: Path, size: int, suf: str) -> tuple[str | None, str]:
    if size < 32:
        return "image_too_small", f"图片过小（{size}B）"
    head = _read_head(path, 16)
    if _looks_plaintext_bytes(head):
        return "plaintext_disguised_as_binary", f"明文冒充 {suf}"
    magic_ok = False
    if suf == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"):
        magic_ok = True
    elif suf in (".jpg", ".jpeg") and head.startswith(b"\xff\xd8\xff"):
        magic_ok = True
    elif suf == ".gif" and head[:3] == b"GIF":
        magic_ok = True
    elif suf == ".webp" and head[:4] == b"RIFF" and b"WEBP" in head[:16]:
        magic_ok = True
    elif suf == ".bmp" and head[:2] == b"BM":
        magic_ok = True
    if not magic_ok:
        return "bad_magic", f"魔数与后缀 {suf} 不符"
    return None, ""


def _check_pdf(path: Path, size: int) -> tuple[str | None, str]:
    if size < 64:
        return "pdf_too_small", f"PDF 过小（{size}B）"
    head = _read_head(path, 8)
    if not head.startswith(b"%PDF"):
        if _looks_plaintext_bytes(head):
            return "plaintext_disguised_as_binary", "明文冒充 .pdf"
        return "bad_magic", "头非 %PDF"
    return None, ""


def _check_zip_only(path: Path, size: int) -> tuple[str | None, str]:
    if size < 22 or not zipfile.is_zipfile(path):
        return "bad_zip", "不是合法 zip"
    return None, ""


def _check_mmd(path: Path, size: int) -> tuple[str | None, str]:
    if size == 0:
        return "empty", "空 .mmd"
    text = path.read_text(encoding="utf-8", errors="replace").lstrip()
    if not text.startswith((
        "graph", "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram",
    )):
        return "bad_mmd", "缺少 mermaid 图类型头关键字"
    return None, ""


def _check_text_doc(path: Path, size: int) -> tuple[str | None, str]:
    if size == 0:
        return "empty", "空文档"
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return "empty", "仅空白"
    if _is_placeholder_text(text):
        return "placeholder", "整文件为占位模板"
    return None, ""


def _check_code(path: Path, size: int, suf: str) -> tuple[str | None, str]:
    if size == 0:
        return "empty", "空源码文件"
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return "empty", "仅空白"
    if _is_placeholder_text(text):
        return "placeholder", "整文件为占位模板"
    if suf == ".json":
        try:
            json.loads(text)
        except ValueError:
            return "bad_json", "JSON 无法解析"
    return None, ""


def _check_binary_claim(path: Path, size: int) -> tuple[str | None, str]:
    if size == 0:
        return "empty", "空文件"
    if _looks_plaintext_header(path):
        return "plaintext_disguised_as_binary", "声称二进制却是明文"
    return None, ""


def _read_head(path: Path, n: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _looks_plaintext_header(path: Path) -> bool:
    return _looks_plaintext_bytes(_read_head(path, 512))


def _looks_plaintext_bytes(data: bytes) -> bool:
    if not data:
        return False
    printable = sum(
        1 for b in data
        if b in (9, 10, 13) or 32 <= b <= 126 or b >= 0x80
    )
    return (printable / len(data)) >= 0.9


def _is_placeholder_text(text: str) -> bool:
    s = text.strip()
    low = s.lower()
    if low.endswith("\n"):
        low = low[:-1].strip()
    if low in _PLACEHOLDER_EXACT:
        return True
    if low.startswith("this is a placeholder.") and len(s) <= 320 and "\n\n" not in s:
        return True
    return False
