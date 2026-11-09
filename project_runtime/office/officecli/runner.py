"""OfficeCLI 子进程加固调用（ADR-044 / 方案 §5.3）。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 禁止的子命令（安装器会改 PATH / 写其他 AI 工具配置）
_FORBIDDEN = frozenset({
    "install", "mcp", "watch", "update", "upgrade", "self-update",
})

DEFAULT_TIMEOUT_S = 120
# health 接受的主版本上限（钉区间；超出不注册）
MIN_MAJOR = 0
MAX_MAJOR = 2


def find_officecli() -> str | None:
    """用户自备路径优先（OFFICECLI_PATH），否则 PATH。"""
    env = (os.environ.get("OFFICECLI_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return str(p.resolve())
        # 允许指向目录内的 officecli(.exe)
        for name in ("officecli.exe", "officecli"):
            cand = p / name
            if cand.is_file():
                return str(cand.resolve())
        return None
    return shutil.which("officecli") or shutil.which("officecli.exe")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["OFFICECLI_SKIP_UPDATE"] = "1"
    env["OFFICECLI_RESIDENT_FLUSH"] = "each"
    return env


def run_officecli(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    binary: str | None = None,
) -> dict[str, Any]:
    """执行 officecli；返回 {ok, stdout, stderr, returncode, error_code?}。"""
    exe = binary or find_officecli()
    if not exe:
        return {
            "ok": False,
            "error_code": "RENDERER_UNAVAILABLE",
            "error": "未配置 OfficeCLI（设 OFFICECLI_PATH 或加入 PATH）",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
    if not args:
        return {
            "ok": False,
            "error_code": "FORBIDDEN_COMMAND",
            "error": "禁止无参数调用 officecli",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
    # 允许版本查询；禁止 install/mcp/watch/update 等
    if args[0] not in ("--version", "-V", "-v", "--help", "-h"):
        sub = args[0].lower()
        if sub in _FORBIDDEN:
            return {
                "ok": False,
                "error_code": "FORBIDDEN_COMMAND",
                "error": f"shim 禁止命令: {args[0]}",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

    cmd = [exe, *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_code": "RENDER_TIMEOUT",
            "error": f"officecli 超时（>{timeout_s}s）",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
    except OSError as e:
        return {
            "ok": False,
            "error_code": "RENDER_FAILED",
            "error": f"无法启动 officecli: {e}",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
        **({} if ok else {
            "error_code": "RENDER_FAILED",
            "error": (proc.stderr or proc.stdout or "officecli failed")[:300],
        }),
    }


def parse_version(text: str) -> tuple[int, int] | None:
    """从 --version 输出解析 (major, minor)。"""
    import re

    m = re.search(r"(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def version_in_range(text: str) -> bool:
    ver = parse_version(text)
    if ver is None:
        return False
    major, _ = ver
    return MIN_MAJOR <= major <= MAX_MAJOR


def assert_within(path: Path, root: Path) -> None:
    resolved = path.resolve()
    root_r = root.resolve()
    try:
        resolved.relative_to(root_r)
    except ValueError as e:
        raise ValueError(f"路径越界: {path}") from e
