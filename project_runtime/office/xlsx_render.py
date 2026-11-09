"""XLSX 渲染：JSON OfficeSpec → openpyxl（O3 加厚）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_xlsx_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    from project_runtime.office.spec import normalize_xlsx_spec

    norm, degraded = normalize_xlsx_spec(spec)
    sheets = norm["sheets"]

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    names: list[str] = []
    for sh in sheets:
        if not isinstance(sh, dict):
            continue
        name = str(sh.get("name") or f"Sheet{len(names) + 1}")[:31]
        ws = wb.create_sheet(title=name)
        names.append(name)
        data = sh.get("data") or sh.get("rows") or []
        if isinstance(data, list):
            for r_i, row in enumerate(data, start=1):
                if isinstance(row, (list, tuple)):
                    for c_i, val in enumerate(row, start=1):
                        ws.cell(row=r_i, column=c_i, value=val)
                elif isinstance(row, dict):
                    for addr, val in row.items():
                        ws[str(addr)] = val

        # 表头样式
        header_style = sh.get("header_style")
        if header_style and isinstance(data, list) and data:
            fill_color = "1F4E79"
            if isinstance(header_style, dict) and header_style.get("fill"):
                fill_color = str(header_style["fill"]).lstrip("#")
            fill = PatternFill("solid", fgColor=fill_color)
            font = Font(bold=True, color="FFFFFF")
            first = data[0]
            ncols = len(first) if isinstance(first, (list, tuple)) else 1
            for c_i in range(1, ncols + 1):
                cell = ws.cell(row=1, column=c_i)
                cell.fill = fill
                cell.font = font

        # 列宽
        widths = sh.get("col_widths") or []
        if isinstance(widths, list):
            for i, w in enumerate(widths, start=1):
                try:
                    col_letter = ws.cell(row=1, column=i).column_letter
                    ws.column_dimensions[col_letter].width = float(w)
                except Exception:  # noqa: BLE001
                    pass

        # 数字格式
        formats = sh.get("number_formats") or {}
        if isinstance(formats, dict):
            for addr, fmt in formats.items():
                try:
                    ws[str(addr)].number_format = str(fmt)
                except Exception:  # noqa: BLE001
                    pass

        # 冻结
        freeze = sh.get("freeze")
        if freeze:
            ws.freeze_panes = str(freeze)

    if not names:
        raise ValueError("未生成任何 sheet")
    wb.save(str(out_path))
    meta: dict[str, Any] = {"sheets": names, "sheet_count": len(names)}
    if degraded:
        meta["degraded_features"] = degraded
    return meta
