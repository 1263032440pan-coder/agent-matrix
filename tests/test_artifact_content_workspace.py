"""产物预览：自定义工作区路径须可通过安全校验。"""

from pathlib import Path

from apps.runtime.api.artifacts import _path_under


def test_path_under_accepts_nested(tmp_path: Path):
    root = tmp_path / "ws"
    child = root / "a" / "b.md"
    child.parent.mkdir(parents=True)
    child.write_text("x", encoding="utf-8")
    assert _path_under(child, root) is True
    assert _path_under(tmp_path / "other" / "x.md", root) is False
