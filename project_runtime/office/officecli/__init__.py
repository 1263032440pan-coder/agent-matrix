"""OfficeCLI 后端支持库（shim 与进程内后端共用）。"""

from project_runtime.office.officecli.batch import spec_to_batch
from project_runtime.office.officecli.runner import (
    find_officecli,
    run_officecli,
)

__all__ = ["spec_to_batch", "find_officecli", "run_officecli"]
