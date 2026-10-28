"""SYS-114: 冻结脚本单调性冒烟。"""

from __future__ import annotations

import pytest

from project_runtime.procedure.delivery_manifest import validate_manifest
from project_runtime.procedure.delivery_validator import (
    DeliveryValidatorError,
    differential_smoke,
    render_match_validator_source,
    write_validator_atomic,
)


def _man():
    return validate_manifest({
        "version": 1,
        "deliverables": [
            {
                "id": "a",
                "name": "A",
                "match": {"ext": [".md"], "name_contains": ["a_"], "min_count": 1},
            },
            {
                "id": "b",
                "name": "B",
                "match": {"ext": [".md"], "name_contains": ["b_"], "min_count": 1},
            },
        ],
    })


@pytest.mark.asyncio
async def test_monotonic_script_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    man = _man()
    src = render_match_validator_source(man)
    path, _ = write_validator_atomic(99, src)
    await differential_smoke(path, man)


@pytest.mark.asyncio
async def test_non_monotonic_script_fails(tmp_path, monkeypatch):
    """「恰好一个 README」类非单调规则 → 冒烟失败。"""
    monkeypatch.chdir(tmp_path)
    man = _man()
    # 非单调：禁止嵌套子目录中的交付文件（子集目录单独验通过，合并根目录失败）
    bad = '''# -*- coding: utf-8 -*-
import argparse, json, sys
from pathlib import Path
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    ws = Path(args.workspace)
    ids = [x for x in args.ids.split(",") if x.strip()]
    files = [p for p in ws.rglob("*") if p.is_file()] if ws.exists() else []
    problems = []
    if not files:
        problems = ["缺文件:" + i for i in ids] or ["缺文件"]
    else:
        nested = any(p.parent.resolve() != ws.resolve() for p in files)
        if nested:
            problems.append("禁止嵌套目录中的交付文件")
        elif len(files) < len(ids):
            problems.append("文件不足")
    ok = len(problems) == 0
    print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False))
    sys.exit(0 if ok else 1)
if __name__ == "__main__":
    main()
'''
    path, _ = write_validator_atomic(98, bad)
    with pytest.raises(DeliveryValidatorError) as ei:
        await differential_smoke(path, man)
    assert "单调" in ei.value.message
