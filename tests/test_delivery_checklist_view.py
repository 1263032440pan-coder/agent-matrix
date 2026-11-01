"""产物清单实时对比：隔离临时工作区。"""

from pathlib import Path

from apps.runtime.api.projects import _delivery_checklist_view
from project_runtime.procedure.delivery_manifest import (
    project_workspace_rel_paths,
)
from project_runtime.project_state.enums import TaskStatus


class _T:
    def __init__(self, id, name, status, playbook_ref):
        self.id = id
        self.name = name
        self.status = status
        self.playbook_ref = playbook_ref


def test_project_workspace_rel_paths(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pptx").write_text("x", encoding="utf-8")
    assert project_workspace_rel_paths(1, root=tmp_path) == ["docs/a.pptx"]


def test_delivery_checklist_matched_and_gap(monkeypatch):
    monkeypatch.setattr(
        "project_runtime.procedure.delivery_manifest.project_workspace_rel_paths",
        lambda project_id, **kw: ["slides/deck.pptx"],
    )
    manifest = {
        "version": 1,
        "deliverables": [
            {
                "id": "d_ppt",
                "name": "方案PPT",
                "match": {"ext": [".pptx"], "min_count": 1},
            },
            {
                "id": "d_md",
                "name": "说明",
                "match": {"ext": [".md"], "min_count": 1},
            },
        ],
    }
    tasks = [
        _T(1, "做PPT", TaskStatus.READY, '{"deliverable_ids":["d_ppt"]}'),
        _T(2, "写说明", TaskStatus.ACCEPTED, '{"deliverable_ids":["d_md"]}'),
    ]
    cl = _delivery_checklist_view(99, manifest, tasks, [])
    assert cl["total"] == 2
    assert cl["matched"] == 1
    by = {i["id"]: i for i in cl["items"]}
    assert by["d_ppt"]["status"] == "matched"
    assert "slides/deck.pptx" in by["d_ppt"]["matched_paths"]
    assert by["d_md"]["status"] == "gap"
    assert by["d_md"]["owner_task_name"] == "写说明"


def test_delivery_checklist_empty_manifest():
    cl = _delivery_checklist_view(1, None, [], [])
    assert cl == {
        "updated_at": None,
        "total": 0,
        "matched": 0,
        "items": [],
    }
