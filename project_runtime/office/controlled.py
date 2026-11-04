"""受控二进制后缀拒写表（write_file / append_file 共用）。"""

from __future__ import annotations

# 后缀 → 建议工具（hint）
CONTROLLED_BINARY_SUFFIXES: dict[str, str] = {
    ".pptx": "office_pptx",
    ".png": "image_raster 或 arch_diagram",
    ".docx": "office_docx",
    ".xlsx": "office_xlsx",
    ".pdf": "office_pdf",
    ".jpg": "image_raster",
    ".jpeg": "image_raster",
    ".webp": "image_raster",
}


def controlled_suffix_of(path: str) -> str | None:
    lower = str(path or "").replace("\\", "/").lower()
    for suf in CONTROLLED_BINARY_SUFFIXES:
        if lower.endswith(suf):
            return suf
    return None


def binary_suffix_denied(
    path: str,
    *,
    visible_tools: list[str] | None = None,
) -> dict:
    from project_runtime.worker.obligation_plugins import binary_denied_hint

    return binary_denied_hint(path, visible_tools=visible_tools)
