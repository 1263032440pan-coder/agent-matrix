"""返修单v2 P5: runtime 看门狗——30s 探活, 失败自动拉起。

用法（常驻后台跑）:
    uv run python scripts/watchdog.py

日志: data/watchdog.log
"""

import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEALTH = "http://127.0.0.1:9100/health"
INTERVAL = 30
LOG = ROOT / "data" / "watchdog.log"


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def archive_death_scene() -> None:
    """返修单 P0-3: 拉起前存死亡现场——runtime 日志尾部 20 行存档。"""
    for src_name in ("runtime_watchdog_out.log", "runtime_last_words.log"):
        src = ROOT / "data" / src_name
        if not src.exists():
            continue
        try:
            lines = src.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            tail = chr(10).join(lines[-20:])
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = ROOT / "data" / f"death_scene_{stamp}.log"
            dst.write_text(
                "=== " + src_name + " tail at death ===" + chr(10)
                + tail + chr(10),
                encoding="utf-8",
            )
        except OSError:
            pass


def start_runtime() -> None:
    archive_death_scene()
    cmd = [
        sys.executable, "-c",
        "from apps.runtime.main import app; import uvicorn; "
        "uvicorn.run(app, host='127.0.0.1', port=9100, reload=False)",
    ]
    out = open(ROOT / "data" / "runtime_watchdog_out.log", "a", encoding="utf-8")
    subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    log("runtime restarted (watchdog)")


def main() -> None:
    log("watchdog started")
    while True:
        if not healthy():
            log("health check FAILED — restarting runtime")
            start_runtime()
            time.sleep(10)  # 拉起后给启动时间
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
