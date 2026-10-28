"""V1.6d: 冻结校验脚本——静态门 / 差分冒烟 / 子进程 / 篡改。"""

from __future__ import annotations

import json

import pytest

from project_runtime.procedure.delivery_manifest import validate_manifest
from project_runtime.procedure.delivery_validator import (
    DeliveryValidatorError,
    clear_validator_files,
    differential_smoke,
    generate_and_install_validator,
    is_validator_script_path,
    render_match_validator_source,
    run_validator_script,
    sha256_file,
    static_check_source,
    validator_path,
    write_validator_atomic,
)


def _man():
    return validate_manifest({
        "version": 1,
        "deliverables": [
            {
                "id": "d_ppt",
                "name": "方案PPT",
                "match": {"ext": [".pptx"], "min_count": 1},
            },
            {
                "id": "d_code",
                "name": "代码",
                "match": {
                    "ext": [".py"],
                    "any_name_prefix": ["readme"],
                    "min_count": 1,
                },
            },
        ],
    })


def test_static_rejects_forbidden_import():
    bad = "import subprocess\nif __name__ == '__main__':\n  pass\n"
    with pytest.raises(DeliveryValidatorError) as ei:
        static_check_source(bad)
    assert ei.value.error_code == "DELIVERY_VALIDATOR_INVALID"


def test_static_requires_main():
    with pytest.raises(DeliveryValidatorError):
        static_check_source("print(1)\n")


def test_static_ok_for_render():
    src = render_match_validator_source(_man())
    static_check_source(src)


@pytest.mark.asyncio
async def test_install_smoke_and_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    man = _man()
    src = render_match_validator_source(man)
    digest = await generate_and_install_validator(
        7, man, source_override=src,
    )
    path = validator_path(7)
    assert path.exists()
    assert sha256_file(path) == digest

    ws = tmp_path / "ws"
    ws.mkdir()
    r = await run_validator_script(path, ws, ["d_ppt"])
    assert r["ok"] is False
    assert r["problems"]

    (ws / "方案.pptx").write_bytes(b"PK")
    r2 = await run_validator_script(path, ws, ["d_ppt"])
    assert r2["ok"] is True


@pytest.mark.asyncio
async def test_differential_smoke_rejects_weak_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 永远 ok 的弱脚本
    weak = '''
import argparse, json, sys
def main():
    argparse.ArgumentParser().parse_args()
    print(json.dumps({"ok": True, "problems": []}))
    sys.exit(0)
if __name__ == "__main__":
    main()
'''
    p, _ = write_validator_atomic(3, weak)
    with pytest.raises(DeliveryValidatorError) as ei:
        await differential_smoke(p, _man())
    assert "空目录" in ei.value.message or "差分" in ei.value.message
    clear_validator_files(3)


def test_is_validator_script_path():
    assert is_validator_script_path("data/tasks/1/_delivery_validate.py")
    assert not is_validator_script_path("data/tasks/1/2/main.py")


@pytest.mark.asyncio
async def test_stop_tamper_raises(tmp_path, monkeypatch):
    """脚本 hash 不符 → DeliveryBaselineError（不降级 match）。"""
    from project_runtime.worker.llm_worker import (
        DeliveryBaselineError,
        LLMWorkerAdapter,
    )

    monkeypatch.chdir(tmp_path)
    man = _man()
    src = render_match_validator_source(man)
    digest = await generate_and_install_validator(
        9, man, source_override=src,
    )
    # 篡改
    validator_path(9).write_text(src + "\n# tampered\n", encoding="utf-8")

    class _Sess:
        async def get(self, model, pid):
            class P:
                delivery_validator_hash = digest
                delivery_manifest = None

            return P()

    class WP:
        task_id = 1
        workspace_root = str(tmp_path / "ws")
        playbook_ref = json.dumps({"deliverable_ids": ["d_ppt"]})
        delivery_tags = None
        code_stage = False

    (tmp_path / "ws").mkdir()
    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    w._current_project_id = 9
    w._session = _Sess()
    w._task_ws_root = lambda: tmp_path / "ws"
    with pytest.raises(DeliveryBaselineError) as ei:
        await w._delivery_stop_problems(WP())
    assert "篡改" in str(ei.value) or "重新生成" in str(ei.value)
