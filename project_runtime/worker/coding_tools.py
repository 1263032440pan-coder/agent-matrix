"""编码三件套 + arch_diagram + todo 插件（20号规范 §4.2/§10.3 批次 3）.

- git:         白名单子命令包装（status/diff/log/show/branch——复用
               shell 白名单纪律, 工作区硬锁, 结构化输出）
- test_runner: pytest 包装: 超时/退出码/失败摘要结构化（供模型自纠）
- builder:     npm build/docker 打包包装（白名单子命令+工作区）
- arch_diagram: mermaid → png 渲染（无渲染器时降级为 .mmd 产物文件）
- todo:        Agent 内任务自管理（低优先——读写 data/tasks/{pid}/.todo.json）

行业插件包 ZIP（§4.4——与 skill 包分发同款三防导入）:
- 防穿越（成员路径不得含 .. 或绝对路径）
- 防覆盖（manifest 声明的 name+version 已存在则拒）
- 防未声明文件（ZIP 成员必须在 manifest 白名单内）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

_OUTPUT_LIMIT = 8000
_PROC_TIMEOUT_S = 600


# ── git（白名单只读 + 常用安全写操作）──────────────────────────────

_GIT_READ = re.compile(r"^(status|diff|log|show|branch)( .*)?$")
_GIT_WRITE = re.compile(
    r"^(add|commit( -m \"[^\"]{1,200}\")?|checkout -b [A-Za-z0-9/_.-]{1,60})$"
)


class GitProvider:
    """git 包装——只读五连 + 白名单写三件（add/commit/checkout -b）。

    写面比 shell 白名单宽一档（Agent 编码环节刚需 commit），
    但参数模式硬锁（-m 只准一条消息、无 && 链）。
    """

    name = "tool:git"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        GitProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        args = str(payload.get("args") or "").strip()
        if not args:
            return {"ok": False, "error": "args 必填（如 'status' / 'diff'）"}
        if _SHELL_FORBIDDEN.search(f"git {args}"):  # noqa: SLF001
            return {"ok": False, "error": "参数含禁用模式"}
        if not (_GIT_READ.match(args) or _GIT_WRITE.match(args)):
            return {
                "ok": False,
                "error": "git 子命令不在白名单"
                         "（只读: status/diff/log/show/branch;"
                         " 写: add / commit -m \"..\" / checkout -b ..）",
            }
        cwd = self._workspace(payload)
        result = await _run_cmd(f"git {args}", cwd)
        if result.get("ok"):
            result["structured"] = _git_structured(args, result.get("stdout", ""))
        return result

    @staticmethod
    def _workspace(payload: dict) -> Path:
        ws = payload.get("workspace")
        if ws:
            try:
                from project_runtime.worker.tools import resolve_workspace_path

                cwd = resolve_workspace_path(str(ws))
                if not cwd.is_dir():
                    cwd.mkdir(parents=True, exist_ok=True)
                return cwd
            except ValueError:
                return Path.cwd()
        return Path.cwd()


def _git_structured(args: str, stdout: str) -> dict:
    """结构化要点（模型自纠的抓手——不止原始输出）。"""
    out: dict = {"subcommand": args.split()[0]}
    if args.startswith("status"):
        out["dirty"] = bool(
            re.search(r"Changes|Untracked", stdout)
        )
        out["branch"] = (re.search(r"on branch (\S+)", stdout, re.I)
                         or [None, "?"])[1] if "branch" in stdout.lower() else "?"
        m = re.search(r"On branch (\S+)", stdout)
        out["branch"] = m.group(1) if m else "?"
    elif args.startswith("log"):
        out["commit_count"] = len(re.findall(r"^commit ", stdout, re.M))
    elif args.startswith("diff"):
        out["files_changed"] = len(re.findall(r"^diff --git", stdout, re.M))
    return out


# ── test_runner（pytest 包装——失败摘要结构化）─────────────────────


class TestRunnerProvider:
    """pytest/vitest 包装: 超时 / 退出码 / 失败摘要（F 行解析）。"""

    name = "tool:test_runner"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        TestRunnerProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        runner = str(payload.get("runner") or "pytest")
        if runner not in ("pytest", "vitest"):
            return {"ok": False, "error": "runner 只支持 pytest/vitest"}
        # target: token 级校验——每个空格分隔 token 必须是纯路径
        # （任何 - 开头的 flag 都拒——-c/--rootdir 配置注入全灭）
        target = str(payload.get("target") or "tests/")
        if not _safe_path_tokens(target):
            return {"ok": False, "error": "target 参数模式不合规（纯路径）"}
        # 参数白名单（路径字符集——禁 -c/--rootdir 配置注入）
        if not re.match(r"^[A-Za-z0-9_/.:\\ -]*$", target):
            return {"ok": False, "error": "target 参数模式不合规"}
        cwd = GitProvider._workspace(payload)  # noqa: SLF001
        cmd = (
            f"pytest {target} -q --no-header"
            if runner == "pytest" else f"npm test -- {target}"
        )
        result = await _run_cmd(cmd, cwd, timeout=_PROC_TIMEOUT_S)
        if "stdout" in result:
            result["summary"] = _parse_pytest(
                result["stdout"], result.get("code", -1)
            )
        return result


def _safe_path_tokens(target: str) -> bool:
    """token 级路径校验: 拒 -flag / 非路径字符 / 空白异常。"""
    if not target:
        return True
    if target != target.strip() or "  " in target:
        return False
    for tok in target.split(" "):
        if not tok or tok.startswith("-"):
            return False
        if not re.match(r"^[A-Za-z0-9_/.:\-]+$", tok):
            return False
    return True


def _parse_pytest(stdout: str, code: int) -> dict:
    """pytest -q 输出 → 结构化（passed/failed/失败用例名）。"""
    m = re.search(r"(\d+) passed", stdout)
    failed_m = re.search(r"(\d+) failed", stdout)
    failed_cases = re.findall(r"^(FAILED\s+\S+)", stdout, re.M)
    return {
        "exit_code": code,
        "passed": int(m.group(1)) if m else 0,
        "failed": int(failed_m.group(1)) if failed_m else 0,
        "failed_cases": failed_cases[:20],
    }


# ── builder（npm build / docker 打包）──────────────────────────────


class BuilderProvider:
    """构建包装——npm run build / docker build（白名单双形态）。"""

    name = "tool:builder"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        BuilderProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        kind = str(payload.get("kind") or "").strip()
        if not kind:
            from project_runtime.worker.tools import tool_error

            return tool_error(
                "MISSING_FIELD", "builder 缺少 kind",
                '示例: builder {"kind": "npm_build"}（可选 npm_build / docker_build）',
            )
        cwd = GitProvider._workspace(payload)  # noqa: SLF001
        if kind == "npm_build":
            cmd = "npm run build"
        elif kind == "docker_build":
            tag = str(payload.get("tag") or "")
            if not re.match(r"^[a-z0-9/:_.-]{1,80}$", tag or "x"):
                return {"ok": False, "error": "tag 参数模式不合规"}
            cmd = f"docker build -t {tag} ." if tag else "docker build ."
        else:
            from project_runtime.worker.tools import tool_error

            return tool_error(
                "INVALID_ACTION",
                "kind 只支持 npm_build/docker_build",
                '示例: builder {"kind": "npm_build"}',
            )
        return await _run_cmd(cmd, cwd, timeout=_PROC_TIMEOUT_S)


# ── arch_diagram（mermaid → png，降级 .mmd 落盘）──────────────────


class ArchDiagramProvider:
    """架构图渲染: mermaid 源码 → png（mmdc 可用时）/ .mmd 产物文件。

    降级纪律: 无渲染器不是失败——.mmd 文件本身就是合法产物
    （Artifact 卡片可预览源码），事件里注明"未渲染"。
    """

    name = "tool:arch_diagram"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        ArchDiagramProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        mermaid = str(payload.get("mermaid") or "").strip()
        name = re.sub(r"[^\w.-]", "_", str(payload.get("name") or "diagram"))
        if not mermaid.startswith(("graph", "flowchart", "sequenceDiagram",
                                   "classDiagram", "stateDiagram")):
            return {"ok": False,
                    "error": "mermaid 必须以 graph/flowchart/sequenceDiagram/"
                             "classDiagram/stateDiagram 开头"}
        from project_runtime.worker.tools import resolve_workspace_path

        try:
            out_dir = resolve_workspace_path(
                str(payload.get("out_dir") or "data/tasks"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        out_dir.mkdir(parents=True, exist_ok=True)
        mmd_path = out_dir / f"{name}.mmd"
        mmd_path.write_text(mermaid, encoding="utf-8")

        # mmdc 可用性探测（10s 冒烟）
        probe = await _run_cmd("mmdc --version", Path.cwd(), timeout=15)
        if not probe.get("ok"):
            return {"ok": True, "rendered": False,
                    "path": str(mmd_path),
                    "note": "mermaid-cli (mmdc) 不可用——已落 .mmd 源码文件"}
        png_path = out_dir / f"{name}.png"
        result = await _run_cmd(
            f'mmdc -i "{mmd_path}" -o "{png_path}"', Path.cwd(),
            timeout=120,
        )
        result.setdefault("path", str(png_path if png_path.exists() else mmd_path))
        result["rendered"] = png_path.exists()
        return result


# ── todo（Agent 任务自管理——低优先简版）────────────────────────────


class TodoProvider:
    """Agent 内待办: 读写工作区 .todo.json（无 ORM——纯文件态）。"""

    name = "tool:todo"
    capability = None
    permission = "READ_ONLY"
    priority = 140
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        TodoProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        from project_runtime.worker.tools import (
            resolve_workspace_path,
            tool_error,
        )

        action = str(payload.get("action") or "").strip()
        if not action:
            return tool_error(
                "MISSING_FIELD", "todo 缺少 action",
                '示例: todo {"action": "add", "text": "..."}（可选 list / add / toggle）',
            )

        try:
            ws = resolve_workspace_path(
                str(payload.get("workspace") or "data/tasks"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        ws.mkdir(parents=True, exist_ok=True)
        todo_path = ws / ".todo.json"
        items: list[dict] = []
        if todo_path.exists():
            try:
                items = json.loads(todo_path.read_text(encoding="utf-8"))
            except ValueError:
                items = []
        if action == "list":
            return {"ok": True, "items": items}
        if action == "add":
            text = str(payload.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "text 必填"}
            items.append({"text": text[:500], "done": False})
        elif action == "toggle":
            raw_idx = payload.get("index")
            idx = int(raw_idx) if raw_idx is not None else -1
            if not (0 <= idx < len(items)):
                return {"ok": False, "error": f"index 越界: {idx}"}
            items[idx]["done"] = not items[idx]["done"]
        else:
            return {"ok": False, "error": "action 只支持 list/add/toggle"}
        todo_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "items": items}


# ── 公共执行器（受限 shell 复用——不另开权限面）────────────────────


async def _run_cmd(command: str, cwd: Path, timeout: int = 120) -> dict:
    import subprocess

    def _blocking() -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            command, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        proc = await asyncio.wait_for(
            asyncio.to_thread(_blocking), timeout=timeout + 10
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"超时（>{timeout}s）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"执行失败: {e}"}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": (proc.stdout or "")[:_OUTPUT_LIMIT],
        "stderr": (proc.stderr or "")[:2000],
    }


# 引 shell 黑名单（同模块纪律——GitProvider 参数防注入复用）
def _import_shell_forbidden() -> re.Pattern[str]:
    from project_runtime.worker.tools import _SHELL_FORBIDDEN

    return _SHELL_FORBIDDEN


_SHELL_FORBIDDEN = _import_shell_forbidden()


# ── 行业插件包 ZIP（§4.4 三防导入——与 skill 包同款）───────────────


class IndustryPackError(Exception):
    """行业插件包导入失败（三防之一触发）。"""


def import_industry_pack(zip_bytes: bytes, plugins_dir: Path) -> dict:
    """导入行业插件包 ZIP——三防: 穿越拒/覆盖拒/未声明拒。

    ZIP 结构约定:
      manifest.json   {"name","version","stage","providers":[文件名...]}
      <provider>.py   Provider 类文件（每文件一个 Provider）
    返回 {"name","version","installed":[路径]}。
    """
    try:
        zf = zipfile.ZipFile(BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise IndustryPackError(f"非法 ZIP: {e}") from e
    names = zf.namelist()

    # 防穿越 + 防绝对路径
    for n in names:
        if n.startswith(("/", "\\")) or ".." in n or ":" in n:
            raise IndustryPackError(f"成员路径非法（穿越/绝对路径）: {n}")

    if "manifest.json" not in names:
        raise IndustryPackError("缺少 manifest.json")
    try:
        manifest = json.loads(
            zf.read("manifest.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise IndustryPackError(f"manifest 解析失败: {e}") from e
    for key in ("name", "version", "providers"):
        if not manifest.get(key):
            raise IndustryPackError(f"manifest 缺字段: {key}")

    # 防未声明: ZIP 成员必须在 manifest 白名单（manifest 自身除外）
    declared = set(manifest["providers"])
    extra = [n for n in names
             if n != "manifest.json" and n not in declared]
    if extra:
        raise IndustryPackError(f"存在未声明文件: {extra[:5]}")
    # 声明了但缺失
    missing = [p for p in declared if p not in names]
    if missing:
        raise IndustryPackError(f"manifest 声明了缺失文件: {missing[:5]}")

    # 防覆盖: name+version 已存在则拒
    target = plugins_dir / f"{manifest['name']}@{manifest['version']}"
    if target.exists():
        raise IndustryPackError(
            f"包已存在: {target.name}（升级请先删除旧版）")

    plugins_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for member in ["manifest.json"] + sorted(declared):
        dest = target / member
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(member))
        installed.append(str(dest))
    return {
        "name": manifest["name"],
        "version": manifest["version"],
        "installed": installed,
    }


def register_batch3_tools(registry) -> None:
    """批次 3 五插件注册（幂等覆盖——与批次 1 register_worker_tools 同款）。"""
    registry.register(GitProvider())
    registry.register(TestRunnerProvider())
    registry.register(BuilderProvider())
    registry.register(ArchDiagramProvider())
    registry.register(TodoProvider())
