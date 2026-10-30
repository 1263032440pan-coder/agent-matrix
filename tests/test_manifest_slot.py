"""SYS-134: name_contains → 类型槽；槽目录验收，不靠中英文件名。"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from project_runtime.procedure.delivery_manifest import (
    check_match,
    validate_manifest,
)
from project_runtime.procedure import delivery_manifest as dm
from project_runtime.procedure.task_load import expand_obligation_tokens
from project_runtime.procedure.token_stop import check_obligation_tokens


def test_name_contains_coerced_to_slot():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d3",
            "name": "需求文档",
            "match": {"name_contains": ["需求"], "min_count": 1},
        }],
    })
    item = man["deliverables"][0]
    assert item["match"]["slot"] == "requirements"
    assert item["match"]["rel_dir"] == "deliverables/requirements"
    assert "name_contains" not in item["match"]
    assert item.get("coerced_to_slot") is True


def test_architecture_needle_to_slot():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d6",
            "name": "系统架构文档",
            "match": {"name_contains": ["架构"], "min_count": 1},
        }],
    })
    assert man["deliverables"][0]["match"]["slot"] == "architecture"


def test_source_ext_without_doc_needle_keeps_no_slot():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d1",
            "name": "项目源码",
            "match": {
                "ext": [".py", ".md"],
                "any_name_prefix": ["readme"],
                "min_count": 1,
            },
        }],
    })
    m = man["deliverables"][0]["match"]
    assert "slot" not in m
    assert ".py" in m["ext"]


def test_expand_slot_token_not_name_token():
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d3",
            "name": "需求文档",
            "match": {"name_contains": ["需求"], "min_count": 1},
        }],
    })
    by_id = {d["id"]: d for d in man["deliverables"]}
    toks = expand_obligation_tokens(
        deliverable_ids=["d3"], deliverables_by_id=by_id,
    )
    ids = [t["id"] for t in toks]
    assert any(i.startswith("slot:d3:requirements") for i in ids)
    assert not any(i.startswith("name:") for i in ids)


def test_token_stop_slot_accepts_english_filename_in_slot_dir(tmp_path: Path):
    slot_dir = tmp_path / "deliverables" / "requirements"
    slot_dir.mkdir(parents=True)
    (slot_dir / "requirements-baseline.md").write_text("ok", encoding="utf-8")
    problems = check_obligation_tokens(
        tmp_path,
        [{"id": "slot:d3:requirements", "weight": 1, "deliverable_id": "d3"}],
        deliverables_by_id={
            "d3": {
                "id": "d3",
                "match": {
                    "slot": "requirements",
                    "rel_dir": "deliverables/requirements",
                    "min_count": 1,
                },
            },
        },
    )
    assert problems == []


def test_token_stop_slot_fails_when_file_outside_slot(tmp_path: Path):
    (tmp_path / "requirements-baseline.md").write_text("ok", encoding="utf-8")
    problems = check_obligation_tokens(
        tmp_path,
        [{"id": "slot:d3:requirements", "weight": 1, "deliverable_id": "d3"}],
    )
    assert any("槽" in p or "slot" in p.lower() or "deliverables" in p for p in problems)


def test_check_match_slot_uses_rel_paths():
    match = {
        "slot": "architecture",
        "rel_dir": "deliverables/architecture",
        "min_count": 1,
    }
    ok = check_match(
        ["deliverables/architecture/architecture-design.md"], match,
    )
    assert ok == []
    bad = check_match(["architecture-design.md"], match)
    assert bad


def test_check_match_slot_accepts_nested_task_or_part_prefix():
    """项目根/冒烟合并：文件在 1/ 或 part_0/ 下仍算落槽。"""
    match = {
        "slot": "requirements",
        "rel_dir": "deliverables/requirements",
        "min_count": 1,
    }
    assert check_match(
        ["1/deliverables/requirements/requirements-baseline.md"], match,
    ) == []
    assert check_match(
        ["part_0/deliverables/solution/fixture.md"],
        {"slot": "solution", "rel_dir": "deliverables/solution", "min_count": 1},
    ) == []


@pytest.mark.asyncio
async def test_slot_manifest_monotonicity_smoke(tmp_path, monkeypatch):
    from project_runtime.procedure.delivery_validator import (
        differential_smoke,
        render_match_validator_source,
        write_validator_atomic,
    )

    monkeypatch.chdir(tmp_path)
    man = validate_manifest({
        "version": 1,
        "deliverables": [
            {
                "id": "d2",
                "name": "解决方案",
                "match": {"name_contains": ["解决方案"], "min_count": 1},
            },
            {
                "id": "d3",
                "name": "需求文档",
                "match": {"name_contains": ["需求"], "min_count": 1},
            },
        ],
    })
    assert man["deliverables"][0]["match"]["slot"] == "solution"
    src = render_match_validator_source(man)
    path, _ = write_validator_atomic(97, src)
    await differential_smoke(path, man)


def test_extract_prompt_mentions_slot():
    src = inspect.getsource(dm.extract_manifest_from_materials)
    assert "slot" in src
    assert "deliverables/" in src
