"""Skill 脚本解释器解析：工作区 / 系统环境（不维护独立 Skill 运行时面板）。

优先：工作区 `.venv` → 系统 Python。
脚本若已自带 PEP 723 且本机有 uv，可走 `uv run`（不改第三方脚本也能受益）。
不再使用 data/skills/runtime/.venv 作为主路径。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def find_workspace_python(cwd: Path | None) -> Path | None:
    """自 cwd 向上找 `.venv` / `venv` 中的 python。"""
    import os

    if cwd is None:
        return None
    cur = cwd.resolve()
    for _ in range(8):
        for name in (".venv", "venv"):
            root = cur / name
            if os.name == "nt":
                cand = root / "Scripts" / "python.exe"
            else:
                cand = root / "bin" / "python"
            if cand.is_file():
                return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def resolve_interpreter_for_script(
    script: Path,
    *,
    cwd: Path | None = None,
) -> tuple[str, list[str]]:
    """返回 (executable, prefix_args)。

    - 脚本含 PEP 723 且本机有 uv → (uv, [run, script])（可选，不要求改 Skill）
    - 否则工作区 venv python + [script]
    - 再否则 sys.executable + [script]
    """
    text_head = ""
    try:
        text_head = script.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        pass
    has_pep723 = "# /// script" in text_head
    uv = shutil.which("uv")
    if has_pep723 and uv:
        return uv, ["run", str(script)]

    ws = find_workspace_python(cwd)
    if ws is not None:
        return str(ws), [str(script)]
    return sys.executable, [str(script)]


# ── 旧共享运行时 API（面板已移除；端点可保留只读兼容）────────────────


RUNTIME_ROOT = Path("data/skills/runtime")
RUNTIME_VENV = RUNTIME_ROOT / ".venv"


def runtime_python() -> Path | None:
    import os

    if os.name == "nt":
        cand = RUNTIME_VENV / "Scripts" / "python.exe"
    else:
        cand = RUNTIME_VENV / "bin" / "python"
    return cand if cand.is_file() else None


def ensure_skill_runtime(*, packages: list[str] | None = None) -> dict:
    """已废弃主路径：不再推荐初始化独立 Skill venv。"""
    return {
        "ok": False,
        "deprecated": True,
        "error": (
            "Skill 共享运行时已废弃；请使用工作区 .venv 或系统 Python。"
            "脚本缺依赖时由模型/工具按报错安装。"
        ),
        "packages_ignored": list(packages or []),
    }


def runtime_status() -> dict:
    return {
        "runtime_root": str(RUNTIME_ROOT.resolve()),
        "venv": str(RUNTIME_VENV.resolve()),
        "python": None,
        "uv": shutil.which("uv"),
        "ready": False,
        "mode": "workspace_or_system",
        "deprecated_shared_runtime": True,
    }
