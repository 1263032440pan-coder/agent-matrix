"""XLSX 渲染：JSON spec → openpyxl。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_xlsx_from_spec(spec: dict, out_path: Path) -> dict[str, Any]:
    from openpyxl import Workbook

    sheets = spec.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise ValueError("spec.sheets 必须为非空数组")

    wb = Workbook()
    # 删掉默认再按 spec 建
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
                    # {A1: value} 风格
                    for addr, val in row.items():
                        ws[str(addr)] = val

    if not names:
        raise ValueError("未生成任何 sheet")
    wb.save(str(out_path))
    return {"sheets": names, "sheet_count": len(names)}
