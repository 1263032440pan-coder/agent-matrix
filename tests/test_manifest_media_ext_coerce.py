"""SYS-133: 等价媒体/图类多 ext → 收束为单一主后缀。"""

from __future__ import annotations

import inspect

from project_runtime.procedure import delivery_manifest as dm
from project_runtime.procedure.delivery_manifest import validate_manifest


def test_prototype_png_jpg_pdf_coerced_to_png():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d4",
            "name": "系统原型图",
            "match": {
                "ext": [".png", ".jpg", ".pdf"],
                "min_count": 1,
            },
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == [".png"]
    assert item.get("coerced_ext_singleton") is True


def test_pdf_alone_not_rewritten():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "报告 PDF",
            "match": {"ext": [".pdf"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == [".pdf"]
    assert not item.get("coerced_ext_singleton")


def test_py_md_not_rewritten():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "说明与脚本",
            "match": {"ext": [".py", ".md"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == [".py", ".md"]
    assert not item.get("coerced_ext_singleton")


def test_jpg_pdf_without_png_keeps_jpg():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d2",
            "name": "流程图",
            "match": {"ext": [".jpg", ".pdf"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == [".jpg"]
    assert item.get("coerced_ext_singleton") is True


def test_extract_prompt_does_not_forbid_multi_media_ext():
    """抽取提示不限制图类多 ext（方案 §4.3）。"""
    src = inspect.getsource(dm.extract_manifest_from_materials)
    for phrase in ("只写一种", "不要堆", "图类只能"):
        assert phrase not in src, f"prompt 不应含限制语: {phrase}"
