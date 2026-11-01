"""SYS-128: 源码类清单项禁止纯压缩 ext。"""

from __future__ import annotations

import pytest

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


def test_source_mixed_ext_drops_archive_and_md():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "源代码",
            "match": {"ext": [".zip", ".py", ".md"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    exts = item["match"]["ext"]
    assert ".zip" not in exts
    assert ".py" in exts
    assert ".md" not in exts
    assert item["match"].get("code_scan") is True
    assert item.get("coerced_source_code") is True


def test_source_code_slot_md_only_does_not_match():
    from project_runtime.procedure.delivery_manifest import check_match

    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "项目源码",
            "match": {
                "ext": [".py", ".md"],
                "slot": "source_code",
                "min_count": 1,
            },
        }],
    })
    m = man["deliverables"][0]["match"]
    assert check_match(
        ["11/deliverables/source_code/设计说明.md"], m,
    ), "仅 md 不应过闸"
    assert not check_match(
        ["11/backend/main.py", "11/deliverables/source_code/设计说明.md"],
        m,
    )


def test_revalidate_preserves_code_scan():
    """计划期纠偏写入 code_scan 后，生成校验脚本前二次 validate 须通过。"""
    first = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "项目源码",
            "match": {"ext": [".py", ".md"], "slot": "source_code", "min_count": 1},
        }],
    })
    assert first["deliverables"][0]["match"].get("code_scan") is True
    second = validate_manifest(first)
    assert second["deliverables"][0]["match"].get("code_scan") is True


@pytest.mark.asyncio
async def test_deterministic_validator_smoke_with_code_scan(tmp_path, monkeypatch):
    """源码 code_scan 清单：确定性脚本须过差分冒烟。"""
    from project_runtime.procedure.delivery_validator import (
        generate_and_install_validator,
        render_match_validator_source,
    )

    monkeypatch.chdir(tmp_path)
    man = validate_manifest({
        "version": 1,
        "deliverables": [
            {
                "id": "d1",
                "name": "项目源码",
                "match": {
                    "ext": [".py", ".md"],
                    "slot": "source_code",
                    "min_count": 1,
                },
            },
            {
                "id": "d2",
                "name": "测试报告",
                "match": {"ext": [".md"], "slot": "testing", "min_count": 1},
            },
        ],
    })
    assert man["deliverables"][0]["match"].get("code_scan") is True
    src = render_match_validator_source(man)
    digest = await generate_and_install_validator(
        42, man, source_override=src,
    )
    assert digest
