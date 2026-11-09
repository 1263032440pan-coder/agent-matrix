"""O5：validate issues 回执 + preview 目录不计交付。"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_runtime.extension.registry import CapabilityType, ExtensionRegistry
from project_runtime.office.facade import append_validate_issues, backend_label
from project_runtime.office.providers import OfficePptxProvider, register_office_tools


class _ValidateBackend:
    name = "office-cli:office.validate"
    capability = CapabilityType.ARTIFACT_PROCESSOR
    permission = "READ_ONLY"
    priority = 200
    enabled = True

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        return {
            "ok": True,
            "issues": [
                {"severity": "warn", "message": "空页", "code": "EMPTY_SLIDE"},
            ],
            "backend": self.name,
        }


@pytest.mark.asyncio
async def test_validate_appended_to_stdout(tmp_path: Path):
    reg = ExtensionRegistry()
    reg.register(_ValidateBackend())
    out = tmp_path / "x.pptx"
    out.write_bytes(b"pk")
    stdout, extra = await append_validate_issues(
        reg, out, ws_root=tmp_path, stdout="已生成 x.pptx",
    )
    assert "【校验】" in stdout
    assert "空页" in stdout
    assert extra["validate"]["issue_count"] == 1
    assert backend_label(extra["validate"]["backend"]) == "office-cli"


@pytest.mark.asyncio
async def test_render_includes_validate(tmp_path: Path):
    reg = ExtensionRegistry()
    register_office_tools(reg)
    reg.register(_ValidateBackend())
    r = await OfficePptxProvider().invoke({
        "path": "deck.pptx",
        "markdown": "# T\n## S\n- a",
        "__ws_root": str(tmp_path),
        "__registry": reg,
    })
    assert r["ok"] is True
    assert "【校验】" in r["stdout"]
    assert r.get("validate", {}).get("issue_count") == 1


def test_workspace_diff_skips_preview(tmp_path: Path):
    """`.matrix/preview/` 不进入 harvest diff。"""
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    (tmp_path / "ok.txt").write_text("a", encoding="utf-8")
    prev = tmp_path / ".matrix" / "preview"
    prev.mkdir(parents=True)
    (prev / "p.png").write_bytes(b"\x89PNG")

    w = object.__new__(LLMWorkerAdapter)
    w._workspace_root = str(tmp_path)
    w._artifact_mgr = None
    w._current_task_id = 1
    diff = w._workspace_diff(set())
    names = {p.name for p in diff}
    assert "ok.txt" in names
    assert "p.png" not in names
