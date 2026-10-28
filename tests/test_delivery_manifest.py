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


def test_validate_coerces_string_match_fields():
    """LLM 把 name_contains/ext 写成单字符串 → 归一为数组。"""
    m = validate_manifest({
        "deliverables": [{
            "id": "d1",
            "name": "测试报告",
            "match": {
                "ext": ".md",
                "name_contains": "测试",
                "min_count": 1,
            },
        }],
    })
    assert m["deliverables"][0]["match"]["ext"] == [".md"]
    assert m["deliverables"][0]["match"]["name_contains"] == ["测试"]


def test_validate_rejects_non_string_list_items():
    with pytest.raises(DeliveryManifestError) as ei:
        validate_manifest({
            "deliverables": [{
                "id": "d1", "name": "X",
                "match": {"name_contains": [1, 2]},
            }],
        })
    assert "name_contains" in ei.value.message


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


def test_llm_message_text_falls_back_to_reasoning():
    from types import SimpleNamespace

    from project_runtime.llm import llm_message_text

    assert llm_message_text(
        SimpleNamespace(content="hi", reasoning_content="think")
    ) == "hi"
    assert llm_message_text(
        SimpleNamespace(content="", reasoning_content='{"a":1}')
    ) == '{"a":1}'
    assert llm_message_text(
        {"content": None, "reasoning_content": "  x  "}
    ) == "x"


@pytest.mark.asyncio
async def test_extract_manifest_uses_reasoning_json(monkeypatch):
    """content 空、reasoning 含 JSON 时仍能抽清单。"""
    from types import SimpleNamespace

    from project_runtime.procedure import delivery_manifest as dm

    class _Cfg:
        base_url = "http://x"
        api_key = "k"
        model = "m"

    async def _fake_complete(**_kwargs):
        msg = SimpleNamespace(
            content="",
            reasoning_content=(
                '思考…最终输出 '
                '{"version":1,"deliverables":['
                '{"id":"d1","name":"方案PPT","match":{"ext":[".pptx"]}}'
                "]}"
            ),
        )
        return {
            "ok": True,
            "response": SimpleNamespace(
                choices=[SimpleNamespace(message=msg)],
            ),
        }

    import project_runtime.llm as llm_mod
    import project_runtime.llm.provider as prov

    monkeypatch.setattr(llm_mod, "load_llm_settings", lambda: _Cfg())
    monkeypatch.setattr(llm_mod, "normalize_model_for_gateway", lambda m, b: m)
    monkeypatch.setattr(prov, "llm_complete", _fake_complete)

    out = await dm.extract_manifest_from_materials(
        materials_text="需要方案PPT",
    )
    assert out["deliverables"][0]["id"] == "d1"


@pytest.mark.asyncio
async def test_stop_problems_uses_frozen_validator(tmp_path, monkeypatch):
    """V1.6d: deliverable_ids → 冻结脚本子进程硬闸。"""
    import json

    from project_runtime.procedure.delivery_manifest import validate_manifest
    from project_runtime.procedure.delivery_validator import (
        generate_and_install_validator,
        render_match_validator_source,
    )
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    monkeypatch.chdir(tmp_path)
    man = validate_manifest({
        "version": 1,
        "deliverables": [{
            "id": "d_ppt",
            "name": "方案PPT",
            "match": {"ext": [".pptx"], "min_count": 1},
        }],
    })
    digest = await generate_and_install_validator(
        1, man, source_override=render_match_validator_source(man),
    )

    class _Sess:
        async def get(self, model, pid):
            class P:
                delivery_manifest = json.dumps(man)
                delivery_validator_hash = digest

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
    assert problems and ("方案PPT" in problems[0] or "pptx" in problems[0].lower())
    (ws / "方案.pptx").write_bytes(b"PK")
    assert await worker._delivery_stop_problems(WP()) == []
