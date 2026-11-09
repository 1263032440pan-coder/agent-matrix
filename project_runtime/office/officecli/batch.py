"""OfficeSpec → OfficeCLI batch JSON 翻译层。"""

from __future__ import annotations

from typing import Any


def spec_to_batch(
    kind: str,
    spec: dict[str, Any] | None,
    *,
    markdown: str | None = None,
) -> list[dict[str, Any]]:
    """生成 officecli batch 命令列表（create 之后在已打开文件上执行）。

    kind: pptx | docx | xlsx
    输出是保守子集；未知字段进 degraded 由调用方记录。
    """
    kind = kind.lower().lstrip(".")
    if kind == "pptx":
        return _pptx_batch(spec, markdown)
    if kind == "docx":
        return _docx_batch(spec or {})
    if kind == "xlsx":
        return _xlsx_batch(spec or {})
    raise ValueError(f"unsupported kind: {kind}")


def _pptx_batch(
    spec: dict[str, Any] | None, markdown: str | None,
) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    if isinstance(spec, dict) and spec.get("slides"):
        title = str(spec.get("title") or "").strip()
        slides = list(spec.get("slides") or [])
        if title:
            ops.append({
                "op": "slide.setTitle",
                "args": {"index": 0, "text": title},
            })
        for i, sl in enumerate(slides):
            if not isinstance(sl, dict):
                continue
            if i > 0:
                ops.append({"op": "slide.add", "args": {}})
            idx = i
            st = str(sl.get("title") or "").strip()
            if st:
                ops.append({
                    "op": "slide.setTitle",
                    "args": {"index": idx, "text": st},
                })
            bullets = sl.get("bullets") or sl.get("points") or []
            if isinstance(bullets, list) and bullets:
                ops.append({
                    "op": "slide.setBullets",
                    "args": {
                        "index": idx,
                        "items": [str(b) for b in bullets],
                    },
                })
            img = sl.get("image")
            if isinstance(img, dict) and img.get("path"):
                ops.append({
                    "op": "slide.addImage",
                    "args": {"index": idx, "path": str(img["path"])},
                })
        return ops
    # markdown 回落：整段交给 setMarkdown（若 CLI 不支持，shim 可降级）
    md = markdown or (spec or {}).get("markdown") or ""
    if md:
        return [{"op": "presentation.fromMarkdown", "args": {"markdown": str(md)}}]
    return [{"op": "slide.setTitle", "args": {"index": 0, "text": "Untitled"}}]


def _docx_batch(spec: dict[str, Any]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    title = str(spec.get("title") or "").strip()
    if title:
        ops.append({"op": "doc.setTitle", "args": {"text": title}})
    for p in spec.get("paragraphs") or []:
        ops.append({"op": "doc.addParagraph", "args": {"text": str(p)}})
    for block in spec.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "paragraph")
        if btype in ("heading", "h1", "h2", "h3"):
            ops.append({
                "op": "doc.addHeading",
                "args": {
                    "text": str(block.get("text") or ""),
                    "level": int(block.get("level") or 1),
                },
            })
        elif btype == "paragraph":
            ops.append({
                "op": "doc.addParagraph",
                "args": {"text": str(block.get("text") or "")},
            })
    for table in spec.get("tables") or []:
        if not isinstance(table, dict):
            continue
        ops.append({
            "op": "doc.addTable",
            "args": {
                "rows": table.get("rows") or table.get("data") or [],
            },
        })
    if not ops:
        ops.append({"op": "doc.addParagraph", "args": {"text": ""}})
    return ops


def _xlsx_batch(spec: dict[str, Any]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    sheets = spec.get("sheets") or []
    for i, sh in enumerate(sheets):
        if not isinstance(sh, dict):
            continue
        name = str(sh.get("name") or f"Sheet{i + 1}")
        if i == 0:
            ops.append({"op": "sheet.rename", "args": {"index": 0, "name": name}})
        else:
            ops.append({"op": "sheet.add", "args": {"name": name}})
        data = sh.get("data") or []
        if data:
            ops.append({
                "op": "sheet.setData",
                "args": {"name": name, "data": data},
            })
        if sh.get("charts"):
            # 仅 office-cli；内置会 degraded
            for ch in sh["charts"]:
                ops.append({
                    "op": "sheet.addChart",
                    "args": {"name": name, "chart": ch},
                })
    if not ops:
        ops.append({"op": "sheet.setData", "args": {"name": "Sheet1", "data": []}})
    return ops
