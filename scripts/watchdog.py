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


def start_runtime() -> None:
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
