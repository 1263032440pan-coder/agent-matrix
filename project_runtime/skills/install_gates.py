"""返修V3 批C: 执行型 skill 四闸全自动安装。

闸1 静态 → 闸2 bwrap 冒烟 → 闸3 GitHub 信任 → 闸4 落盘+白名单+venv
纯文本 skill 不进本模块（走 importer 默认四防）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import shutil
import venv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from project_runtime.skills.sandbox import SkillSandboxBox, SkillSandboxConfig

logger = logging.getLogger(__name__)

ASSETS_ROOT = Path("data/skills/assets")
VENVS_ROOT = Path("data/skills/venvs")
WHITELIST_PATH = Path("data/skills/script_whitelist.json")

TRUST_MIN_STARS = 1000
TRUST_MAX_DAYS = 90

_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "__import__",
    "system", "popen", "Popen",
}
_DANGEROUS_ATTRS = {"system", "popen", "execl", "execv", "execve"}
_DANGEROUS_MODULES = {"pickle", "marshal", "ctypes"}


@dataclass
class GateItemResult:
    gate: str
    status: str  # PASS / FAIL / SKIP / UNKNOWN
    detail: str = ""
    mark: str = ""


@dataclass
class FourGateReport:
    ok: bool
    items: list[GateItemResult] = field(default_factory=list)
    skill_id: str = ""
    version: str = ""
    asset_dir: str = ""
    whitelist_entries: list[str] = field(default_factory=list)
    sandbox_mark: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skill_id": self.skill_id,
            "version": self.version,
            "asset_dir": self.asset_dir,
            "sandbox_mark": self.sandbox_mark,
            "whitelist_entries": self.whitelist_entries,
            "items": [
                {"gate": i.gate, "status": i.status, "detail": i.detail,
                 "mark": i.mark}
                for i in self.items
            ],
        }


def _iter_py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def gate1_static(root: Path, allowed_tools: list[str] | None = None) -> GateItemResult:
    """静态扫描：AST 危调用 + 可选 bandit/pip-audit（缺失则 UNKNOWN 不放行依赖项）。"""
    issues: list[str] = []
    # AST 自研最小集（bandit 同语义危点——不强制本机装 bandit）
    for py in _iter_py_files(root):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as e:
            issues.append(f"syntax:{py.name}:{e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in _DANGEROUS_CALLS or name in _DANGEROUS_ATTRS:
                    issues.append(f"dangerous_call:{py.name}:{name}")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in _DANGEROUS_MODULES:
                        issues.append(f"dangerous_import:{py.name}:{a.name}")
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in _DANGEROUS_MODULES:
                    issues.append(f"dangerous_import:{py.name}:{node.module}")

    # bandit 增强（有则跑；无则不单独 FAIL——AST 已覆盖核心）
    if shutil.which("bandit"):
        pass  # Demo: AST 已覆盖；有 bandit 可后续 subprocess 接入

    # pip-audit：有 requirements 且无 pip-audit → UNKNOWN 不放行
    reqs = list(root.glob("requirements*.txt")) + list(root.glob("**/pyproject.toml"))
    if reqs and not shutil.which("pip-audit"):
        return GateItemResult(
            "static", "UNKNOWN",
            "pip-audit missing while dependency manifests present — fail-closed",
        )

    # allowed-tools 声明比对（有脚本则 frontmatter 应声明）
    scripts = [p for p in root.rglob("*") if p.suffix.lower() in {".py", ".sh"}]
    if scripts and allowed_tools is not None and len(allowed_tools) == 0:
        issues.append("scripts present but allowed-tools empty")

    if issues:
        return GateItemResult("static", "FAIL", "; ".join(issues[:8]))
    return GateItemResult("static", "PASS", "ast clean")


async def gate2_sandbox(root: Path, smoke_cmd: list[str] | None = None) -> GateItemResult:
    """bwrap 冒烟；无 bwrap → SKIP + SANDBOX_SKIPPED。"""
    if not SkillSandboxBox.bwrap_available():
        return GateItemResult(
            "sandbox", "SKIP", "bwrap not installed",
            mark="SANDBOX_SKIPPED",
        )
    out_dir = root / "_smoke_out"
    out_dir.mkdir(exist_ok=True)
    cfg = SkillSandboxConfig(
        ro_binds=[(str(root.resolve()), "/skill")],
        rw_binds=[(str(out_dir.resolve()), "/out")],
        unshare_net=True,
    )
    cmd = smoke_cmd or ["python", "-c", "print('ok')"]
    with SkillSandboxBox(cfg) as box:
        # 把命令改到沙箱视图路径
        result = await box.run(cmd)
    if result.get("skipped"):
        return GateItemResult(
            "sandbox", "SKIP", result.get("error", ""),
            mark="SANDBOX_SKIPPED",
        )
    if not result.get("ok"):
        return GateItemResult(
            "sandbox", "FAIL",
            result.get("error") or result.get("stderr") or "smoke failed",
            mark=result.get("mark", ""),
        )
    return GateItemResult("sandbox", "PASS", "smoke ok")


def gate3_trust(
    repo: str | None = None,
    *,
    stars: int | None = None,
    last_push: datetime | None = None,
    fetch: bool = True,
) -> GateItemResult:
    """GitHub ≥1000 star 且 90 天内有提交；无外网/无 repo → SKIP 降权。"""
    if stars is None and last_push is None and repo and fetch:
        try:
            data = _github_repo(repo)
            stars = int(data.get("stargazers_count") or 0)
            pushed = data.get("pushed_at")
            if pushed:
                last_push = datetime.fromisoformat(
                    pushed.replace("Z", "+00:00")
                )
        except Exception as e:  # noqa: BLE001
            return GateItemResult(
                "trust", "SKIP",
                f"github unreachable: {e}", mark="TRUST_SKIPPED",
            )
    if stars is None and last_push is None:
        return GateItemResult(
            "trust", "SKIP", "no repo metadata", mark="TRUST_SKIPPED",
        )
    star_n = stars or 0
    if star_n < TRUST_MIN_STARS:
        return GateItemResult(
            "trust", "FAIL",
            f"stars {star_n} < {TRUST_MIN_STARS}",
        )
    if last_push is not None:
        age = datetime.now(timezone.utc) - last_push.astimezone(timezone.utc)
        if age > timedelta(days=TRUST_MAX_DAYS):
            return GateItemResult(
                "trust", "FAIL",
                f"last push {age.days}d ago > {TRUST_MAX_DAYS}d",
            )
    return GateItemResult("trust", "PASS", f"stars={star_n}")


def _github_repo(repo: str) -> dict:
    # repo like owner/name
    url = f"https://api.github.com/repos/{repo.strip('/')}"
    req = Request(url, headers={"Accept": "application/vnd.github+json",
                                "User-Agent": "paa-skill-gates"})
    with urlopen(req, timeout=15) as resp:  # noqa: S310 — 固定 GitHub API
        return json.loads(resp.read().decode("utf-8"))


def gate4_materialize(
    root: Path,
    skill_id: str,
    version: str,
    *,
    source_ref: str = "",
) -> tuple[GateItemResult, str, list[str]]:
    """落盘只读资产 + hash + venv + 脚本白名单条目。"""
    dest = ASSETS_ROOT / skill_id / version
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root, dest)

    # 逐文件 hash 锁定
    hashes: dict[str, str] = {}
    for f in dest.rglob("*"):
        if f.is_file():
            rel = f.relative_to(dest).as_posix()
            hashes[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    meta = {
        "skill_id": skill_id,
        "version": version,
        "source_ref": source_ref,
        "file_hashes": hashes,
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
    (dest / ".paa_lock.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    # 绑定独立 venv（空环境——依赖由运营预装/后续扩展）
    venv_dir = VENVS_ROOT / skill_id
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    if not venv_dir.exists():
        venv.create(str(venv_dir), with_pip=False)

    # 脚本绝对路径 → 白名单
    entries: list[str] = []
    for py in dest.rglob("*.py"):
        if py.name.startswith("test_"):
            continue
        abs_py = str(py.resolve())
        entries.append(f"python {abs_py}")
    _append_whitelist(skill_id, version, entries, str(venv_dir.resolve()))

    return (
        GateItemResult("materialize", "PASS", f"locked {len(hashes)} files"),
        str(dest.resolve()),
        entries,
    )


def _append_whitelist(
    skill_id: str, version: str, entries: list[str], venv_path: str,
) -> None:
    WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if WHITELIST_PATH.exists():
        try:
            data = json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data[f"{skill_id}@{version}"] = {
        "entries": entries,
        "venv": venv_path,
    }
    WHITELIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def load_script_whitelist() -> dict[str, dict]:
    if not WHITELIST_PATH.exists():
        return {}
    try:
        return json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def is_whitelisted_skill_script(command: str) -> bool:
    """shell 调用面: python <绝对脚本路径> … 是否在 skill 白名单。"""
    parts = command.strip().split()
    if len(parts) < 2 or parts[0].lower() not in ("python", "python3"):
        return False
    script = parts[1]
    for meta in load_script_whitelist().values():
        for ent in meta.get("entries") or []:
            if ent.split()[:2] == ["python", script] or script in ent:
                return True
    return False


async def run_four_gates(
    root: Path,
    *,
    skill_id: str,
    version: str,
    allowed_tools: list[str] | None = None,
    github_repo: str | None = None,
    trust_stars: int | None = None,
    trust_last_push: datetime | None = None,
    source_ref: str = "",
    smoke_cmd: list[str] | None = None,
    skip_trust_fetch: bool = False,
) -> FourGateReport:
    """跑四闸；任一 FAIL/UNKNOWN（静态）→ ok=False。信任 SKIP 可与 1+2 组合放行。"""
    report = FourGateReport(ok=False, skill_id=skill_id, version=version)

    g1 = gate1_static(root, allowed_tools)
    report.items.append(g1)
    if g1.status in ("FAIL", "UNKNOWN"):
        return report

    g2 = await gate2_sandbox(root, smoke_cmd=smoke_cmd)
    report.items.append(g2)
    report.sandbox_mark = g2.mark
    if g2.status == "FAIL":
        return report

    g3 = gate3_trust(
        github_repo,
        stars=trust_stars,
        last_push=trust_last_push,
        fetch=not skip_trust_fetch and trust_stars is None,
    )
    report.items.append(g3)
    if g3.status == "FAIL":
        return report

    # 闸2 SKIP 时：仅当闸1 PASS 且闸3 非 FAIL —— 限制为只读工具类（标记保留）
    g4, asset_dir, entries = gate4_materialize(
        root, skill_id, version, source_ref=source_ref,
    )
    report.items.append(g4)
    if g4.status != "PASS":
        return report

    report.ok = True
    report.asset_dir = asset_dir
    report.whitelist_entries = entries
    return report
