"""SYS-128: 源码类清单项禁止纯压缩 ext。"""

from __future__ import annotations

from project_runtime.procedure.delivery_manifest import (
    DEFAULT_SOURCE_EXTS,
    validate_manifest,
)


def test_source_item_pure_zip_rewritten_to_code_exts():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d10",
            "name": "项目源码",
            "match": {
                "ext": [".zip"],
                "name_contains": ["源码"],
                "any_name_prefix": ["readme"],
                "min_count": 1,
            },
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == list(DEFAULT_SOURCE_EXTS)
    assert item.get("coerced_from_archive") is True
    assert "readme" in (item["match"].get("any_name_prefix") or [])


def test_release_zip_not_rewritten():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "发行包",
            "match": {"ext": [".zip"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["ext"] == [".zip"]
    assert not item.get("coerced_from_archive")


def test_source_mixed_ext_drops_archive_keeps_code():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "源代码",
            "match": {"ext": [".zip", ".py", ".md"], "min_count": 1},
        }],
    })
    exts = man["deliverables"][0]["match"]["ext"]
    assert ".zip" not in exts
    assert ".py" in exts and ".md" in exts
