"""义务 → 所需插件 + 缺口计算（修改方案-义务驱动工具绑定）。"""

from __future__ import annotations

import json

from project_runtime.worker.obligation_plugins import (
    plugin_gap,
    required_plugins_for_playbook_ref,
    required_plugins_for_tokens,
)


def test_pptx_token_requires_office_pptx():
    need = required_plugins_for_tokens([
        {"id": "ext:d1", "exts": [".pptx"], "label": ".pptx"},
    ])
    assert need == {"office_pptx"}


def test_mixed_office_and_image_tokens():
    need = required_plugins_for_tokens([
        {"exts": [".docx"]},
        {"exts": [".png", ".jpg"]},
        {"exts": [".xlsx"]},
        {"exts": [".pdf"]},
    ])
    assert need == {
        "office_docx", "image_raster", "office_xlsx", "office_pdf",
    }


def test_playbook_ref_obligation_tokens():
    ref = json.dumps({
        "obligation_tokens": [
            {"exts": [".PPTX"]},  # case insensitive
        ],
        "delivery_tags": ["code"],
    })
    assert required_plugins_for_playbook_ref(ref) == {"office_pptx"}


def test_playbook_ref_deliverable_ids_with_manifest():
    man = {
        "deliverables": [
            {"id": "d1", "match": {"ext": [".pptx"]}},
            {"id": "d2", "match": {"ext": [".py"]}},
        ],
    }
    ref = json.dumps({"deliverable_ids": ["d1", "d2"]})
    assert required_plugins_for_playbook_ref(ref, manifest=man) == {
        "office_pptx",
    }


def test_plugin_gap_detects_missing_office():
    need = {"office_pptx", "web_search"}
    have = ["file_rw", "web_search"]  # expands to file tools + web
    gap = plugin_gap(need, have)
    assert gap == {"office_pptx"}


def test_no_gap_when_office_present():
    need = {"office_pptx"}
    have = ["read_file", "office_pptx", "web_search"]
    assert plugin_gap(need, have) == set()


def test_empty_tokens_no_plugins():
    assert required_plugins_for_tokens([]) == set()
    assert required_plugins_for_playbook_ref("{}") == set()


def test_detect_code_media_mismatch_covers_office_and_image():
    from project_runtime.worker.obligation_plugins import (
        detect_code_media_mismatch,
        detect_code_ppt_mismatch,
    )

    assert detect_code_ppt_mismatch({
        "delivery_tags": ["code"],
        "obligation_tokens": [{"exts": [".pptx"]}],
    }) == "CODE_PPT_MISMATCH"
    assert detect_code_media_mismatch({
        "delivery_tags": ["code"],
        "obligation_tokens": [{"exts": [".docx"]}],
    }) == "CODE_MEDIA_MISMATCH"
    assert detect_code_media_mismatch({
        "delivery_tags": ["code"],
        "obligation_tokens": [{"exts": [".png"]}],
    }) == "CODE_MEDIA_MISMATCH"
    assert detect_code_media_mismatch({
        "delivery_tags": ["ppt"],
        "obligation_tokens": [{"exts": [".pptx"]}],
    }) is None


def test_resolve_code_media_splits_graph():
    from project_runtime.worker.obligation_plugins import (
        resolve_code_media_mismatches,
    )

    graph = {
        "nodes": {
            "实现": {
                "task_key": "实现",
                "obligation_tokens": [
                    {"id": "t1", "exts": [".pptx"]},
                    {"id": "t2", "exts": [".png"]},
                ],
                "deliverable_ids": ["d1", "d2"],
                "playbook_ref": {
                    "delivery_tags": ["code"],
                    "deliverable_ids": ["d1", "d2"],
                    "obligation_tokens": [
                        {"id": "t1", "exts": [".pptx"]},
                        {"id": "t2", "exts": [".png"]},
                    ],
                },
                "frozen": False,
            },
        },
        "deps": [],
    }
    man = {
        "deliverables": [
            {"id": "d1", "match": {"ext": [".pptx"]}},
            {"id": "d2", "match": {"ext": [".png"]}},
        ],
    }
    stats = resolve_code_media_mismatches(graph, manifest=man)
    assert stats["code_media_split"] == 1
    assert any(k.startswith("交付补全-办公-") for k in graph["nodes"])
    assert graph["nodes"]["实现"]["obligation_tokens"] == []
    child = next(
        v for k, v in graph["nodes"].items() if k.startswith("交付补全-")
    )
    assert {t["id"] for t in child["obligation_tokens"]} == {"t1", "t2"}


def test_binary_denied_lists_visible_tools():
    from project_runtime.office.controlled import binary_suffix_denied

    r = binary_suffix_denied(
        "a.pptx", visible_tools=["write_file", "web_search"],
    )
    assert r["error_code"] == "BINARY_SUFFIX_DENIED"
    assert "office_pptx" in (r.get("hint") or "")
    assert "当前无 office_pptx" in (r.get("hint") or "")


def test_stage_alias_feature_impl_is_aux_only():
    """别名辅改；即使无别名，义务门禁仍成立（T7）。"""
    from project_runtime.worker.registry import normalize_stage

    assert normalize_stage("功能实现") == "开发实现"
