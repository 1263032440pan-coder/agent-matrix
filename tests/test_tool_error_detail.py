"""T3 / V19：出错按小节补充说明。"""

from __future__ import annotations

from project_runtime.worker.tool_surface import attach_error_detail


def _card():
    return {
        "name": "tool:office_pptx",
        "detail": {
            "overview": "总览说明",
            "images": "图片字段说明",
        },
        "error_sections": {
            "INVALID_SPEC": "overview",
            "INVALID_SPEC.images": "images",
        },
    }


def test_first_error_gets_section():
    seen: set[str] = set()
    t = attach_error_detail(
        _card(), "INVALID_SPEC.images", prior_count=0, seen_keys=seen,
    )
    assert t == "图片字段说明"


def test_second_error_gets_full_detail():
    seen: set[str] = set()
    attach_error_detail(
        _card(), "INVALID_SPEC", prior_count=0, seen_keys=seen,
    )
    t2 = attach_error_detail(
        _card(), "INVALID_SPEC", prior_count=1, seen_keys=seen,
    )
    assert t2 is not None
    assert "总览说明" in t2
    assert "图片字段说明" in t2


def test_third_error_no_more_detail():
    seen: set[str] = set()
    assert attach_error_detail(
        _card(), "INVALID_SPEC", prior_count=2, seen_keys=seen,
    ) is None
