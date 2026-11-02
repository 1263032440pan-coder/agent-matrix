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


def binary_suffix_denied(path: str) -> dict:
    from project_runtime.worker.tools import tool_error

    suf = controlled_suffix_of(path) or ""
    tool = CONTROLLED_BINARY_SUFFIXES.get(suf, "专用渲染工具")
    return tool_error(
        "BINARY_SUFFIX_DENIED",
        f"禁止用 write_file/append_file 写入 {suf or '二进制'}——"
        "请只写 UTF-8 规格（.json/.md），再调用专用工具生成",
        f"请改用 {tool}，path 使用清单相对路径（如 deliverables/.../文件{suf}）",
    )
