"""V1.6c: 动态交付清单 schema / match / 非法 JSON。"""

import pytest

from project_runtime.procedure.delivery_manifest import (
    DeliveryManifestError,
    assign_ids_heuristic,
    check_deliverables,
    check_match,
    map_legacy_tags_to_ids,
    validate_manifest,
)


def test_validate_ok():
    m = validate_manifest({
        "version": 1,
        "deliverables": [
            {
                "id": "d1",
                "name": "方案PPT",
                "match": {"ext": ["pptx"], "min_count": 1},
            },
            {
                "id": "d2",
                "name": "可运行代码",
                "match": {
                    "ext": [".py"],
                    "any_name_prefix": ["readme"],
                    "min_count": 1,
                },
            },
        ],
    })
    assert m["deliverables"][0]["match"]["ext"] == [".pptx"]
    assert len(m["deliverables"]) == 2


def test_validate_unknown_match_key():
    with pytest.raises(DeliveryManifestError) as ei:
        validate_manifest({
            "deliverables": [{
                "id": "d1", "name": "X",
                "match": {"ext": [".md"], "foo": 1},
            }],
        })
    assert ei.value.error_code == "DELIVERY_MANIFEST_INVALID"
    assert "foo" in ei.value.message


def test_validate_bad_json_string():
    with pytest.raises(DeliveryManifestError):
        validate_manifest("{not json")


def test_check_match_pptx():
    assert check_match(["a.md"], {"ext": [".pptx"], "min_count": 1})
    assert check_match(
        ["方案.pptx"], {"ext": [".pptx"], "min_count": 1},
    ) == []


def test_check_match_code_readme():
    m = {
        "ext": [".py"],
        "any_name_prefix": ["readme"],
        "min_count": 1,
    }
    assert check_match(["main.py"], m)
    assert check_match(["main.py", "README.md"], m) == []


def test_check_deliverables_and_legacy_map():
    man = validate_manifest({
        "deliverables": [
            {"id": "d_ppt", "name": "方案PPT",
             "match": {"ext": [".pptx"]}},
            {"id": "d_code", "name": "代码",
             "match": {"ext": [".py"], "any_name_prefix": ["readme"]}},
        ],
    })
    assert map_legacy_tags_to_ids(man, ["ppt"]) == ["d_ppt"]
    probs = check_deliverables(["x.md"], man, ["d_ppt"])
    assert probs and "方案PPT" in probs[0]


def test_assign_heuristic():
    man = validate_manifest({
        "deliverables": [
            {"id": "d1", "name": "方案PPT", "match": {"ext": [".pptx"]}},
        ],
    })
    ids = assign_ids_heuristic(
        "制作方案PPT", ["方案PPT"], [], man,
    )
    assert "d1" in ids


@pytest.mark.asyncio
async def test_stop_problems_uses_manifest_ids(tmp_path):
    """有 delivery_manifest + deliverable_ids → match 硬闸（非写死 tags）。"""
    import json

    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    class _Sess:
        async def get(self, model, pid):
            class P:
                delivery_manifest = json.dumps({
                    "version": 1,
                    "deliverables": [{
                        "id": "d_ppt",
                        "name": "方案PPT",
                        "match": {"ext": [".pptx"], "min_count": 1},
                    }],
                })

            return P()

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "readme.md").write_text("x", encoding="utf-8")

    class WP:
        task_id = 1
        workspace_root = str(ws)
        playbook_ref = json.dumps({"deliverable_ids": ["d_ppt"]})
        delivery_tags = None
        code_stage = False

    worker = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    worker._current_project_id = 1
    worker._session = _Sess()
    worker._task_ws_root = lambda: ws
    problems = await worker._delivery_stop_problems(WP())
    assert problems and "方案PPT" in problems[0]
    (ws / "方案.pptx").write_bytes(b"PK")
    assert await worker._delivery_stop_problems(WP()) == []
