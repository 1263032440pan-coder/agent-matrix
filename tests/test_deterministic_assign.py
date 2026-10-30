"""SYS-130: 确定性兜底分配必过 SYS-109/110。"""

from __future__ import annotations

import pytest

from project_runtime.procedure.delivery_assignment import (
    deterministic_assign,
    validate_assignment_hard_gate,
)


@pytest.mark.asyncio
async def test_deterministic_assign_passes_package_at_tail():
    """再现失败形态：多 document + source，package 须在链尾。"""
    tasks = [
        {"task_key": "理解", "name": "理解"},
        {"task_key": "Worker执行", "name": "Worker执行"},
        {"task_key": "测试", "name": "测试"},
        {"task_key": "交付", "name": "交付"},
    ]
    topo = ["理解", "Worker执行", "测试", "交付"]
    roles = {
        "理解": "process",
        "Worker执行": "implement",
        "测试": "verify",
        "交付": "deliver",
    }
    manifest = {
        "deliverables": [
            {"id": "d1", "name": "需求文档", "match": {"ext": [".md"]}},
            {"id": "d7", "name": "测试报告", "match": {"ext": [".md"]}},
            {"id": "d10", "name": "项目源码",
             "match": {"ext": [".py"], "min_count": 1}},
            {"id": "dp", "name": "发行包", "match": {"ext": [".zip"]}},
        ],
    }
    categories = {
        "d1": {"category": "document"},
        "d7": {"category": "verification"},
        "d10": {"category": "source"},
        "dp": {"category": "package"},
    }
    assignment = deterministic_assign(
        manifest=manifest,
        tasks=tasks,
        topo_order=topo,
        roles=roles,
        categories=categories,
    )
    cats_list = [
        {"id": k, "category": v["category"]} for k, v in categories.items()
    ]
    problems = validate_assignment_hard_gate(
        assignment=assignment,
        manifest=manifest,
        tasks=tasks,
        categories=cats_list,
        roles=roles,
        topo_order=topo,
    )
    assert problems == []
    assert assignment["d10"] == "Worker执行"
    assert assignment["dp"] == "交付"
    assert assignment["d7"] == "测试"


@pytest.mark.asyncio
async def test_run_assignment_bad_llm_falls_back():
    from project_runtime.procedure.delivery_assignment import run_assignment

    tasks = [
        {"task_key": "理解", "name": "理解"},
        {"task_key": "Worker执行", "name": "Worker执行"},
        {"task_key": "交付", "name": "交付"},
    ]
    topo = ["理解", "Worker执行", "交付"]
    manifest = {
        "deliverables": [
            {"id": "d1", "name": "文档", "match": {"ext": [".md"]}},
            {"id": "d2", "name": "项目源码",
             "match": {"ext": [".py"], "min_count": 1}},
            {"id": "d3", "name": "发行包", "match": {"ext": [".zip"]}},
        ],
    }

    async def bad_llm(_prompt: str) -> str:
        return "not-json"

    assignment, mode = await run_assignment(
        manifest=manifest,
        tasks=tasks,
        topo_order=topo,
        llm=bad_llm,
    )
    assert mode == "deterministic_fallback"
    assert set(assignment.keys()) == {"d1", "d2", "d3"}
    assert assignment["d2"] == "Worker执行"
    assert assignment["d3"] == "交付"
