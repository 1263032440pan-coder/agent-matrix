"""返修V3 批C 闸2: bwrap 沙箱（Codex/bubblewrap-ai 策略语义抄件）。

无 Docker；bwrap 缺失 → 调用方标 SANDBOX_SKIPPED（本模块只报告）。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SkillSandboxConfig:
    """沙箱策略字段面（对照 Codex CLI 语义，无容器概念）。"""

    ro_binds: list[tuple[str, str]] = field(default_factory=list)
    # (host_path, sandbox_path)
    rw_binds: list[tuple[str, str]] = field(default_factory=list)
    unshare_net: bool = True
    unshare_pid: bool = True
    die_with_parent: bool = True
    timeout_s: int = 120
    mem_mb: int = 512
    max_procs: int = 64


class SkillSandboxBox:
    """组装 bwrap 参数并跑冒烟命令；退出清理临时目录。"""

    def __init__(self, config: SkillSandboxConfig | None = None):
        self.config = config or SkillSandboxConfig()
        self._tmpdir: Path | None = None

    @staticmethod
    def bwrap_available() -> bool:
        return shutil.which("bwrap") is not None

    def __enter__(self) -> "SkillSandboxBox":
        self._tmpdir = Path(tempfile.mkdtemp(prefix="skill_sbx_"))
        return self

    def __exit__(self, *exc) -> None:
        if self._tmpdir and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None

    @property
    def work_dir(self) -> Path:
        assert self._tmpdir is not None
        return self._tmpdir

    def build_argv(self, command: list[str]) -> list[str]:
        """bubblewrap-ai 风格参数模板。"""
        cfg = self.config
        argv = ["bwrap"]
        if cfg.die_with_parent:
            argv.append("--die-with-parent")
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        if cfg.unshare_net:
            argv.append("--unshare-net")
        if cfg.unshare_pid:
            argv.append("--unshare-pid")
        for host, guest in cfg.ro_binds:
            argv += ["--ro-bind", host, guest]
        for host, guest in cfg.rw_binds:
            argv += ["--bind", host, guest]
        inner_cmd = (
            f"ulimit -v {cfg.mem_mb * 1024}; ulimit -u {cfg.max_procs}; "
            + " ".join(shlex_quote(c) for c in command)
        )
        argv += ["bash", "-lc", inner_cmd]
        return argv

    async def run(self, command: list[str]) -> dict:
        if not self.bwrap_available():
            return {
                "ok": False,
                "skipped": True,
                "mark": "SANDBOX_SKIPPED",
                "error": "bwrap not found",
            }
        if self._tmpdir is None:
            raise RuntimeError("SkillSandboxBox not entered")
        argv = self.build_argv(command)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._tmpdir),
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=self.config.timeout_s,
                )
            except TimeoutError:
                proc.kill()
                return {"ok": False, "error": "sandbox timeout", "mark": "TIMEOUT"}
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": (out or b"").decode("utf-8", errors="replace")[:4000],
                "stderr": (err or b"").decode("utf-8", errors="replace")[:2000],
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("bwrap run failed", exc_info=True)
            return {"ok": False, "error": str(e)}


def shlex_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)
