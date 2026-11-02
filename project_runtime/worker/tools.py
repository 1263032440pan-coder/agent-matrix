"""Worker 底座插件（20号规范 §4.1——批次 1 五件）.

全部 TOOL 能力 Provider（D16 契约），注册进 shared_registry，
经 Worker 工具循环由模型自决调用（描述即调用策略——§4.3）。

- webfetch:        拉取指定 URL（markdown/text）
- read_file / list_dir / write_file: 产物读写（读 data/tasks 内，写本任务工作区）
- shell:           白名单受限执行（cwd 硬锁任务工作区 + 参数模式白名单）
- code_search:     代码检索（文件名 glob + 内容 grep 简版，ripgrep 批次 3）
- slide_generator: md → .pptx（python-pptx——批次 1 刚需）
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path("data/tasks")  # 相对——_resolve 动态解析（可注入绝对根）
_HTTP_TIMEOUT = 20
_OUTPUT_LIMIT = 8000

# ── webfetch ───────────────────────────────────────────────────────


class WebFetchProvider:
    """拉取指定 URL 文档（RESEARCH 能力——与搜索 provider 同面注册）。"""

    name = "tool:webfetch"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        WebFetchProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        url = str(payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "URL 必须以 http(s):// 开头"}

        def _blocking() -> str:
            req = urllib.request.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                )
            })
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                return r.read().decode("utf-8", errors="replace")

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_blocking), timeout=_HTTP_TIMEOUT + 5
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"抓取失败: {e}"}
        # 粗剥 HTML 标签（批次 1 简版；markdown 转换批次 3）
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>",
                      "", raw)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return {"ok": True, "url": url, "content": text[:_OUTPUT_LIMIT]}


# ── 文件工具（read_file / list_dir / write_file——工作区禁闭）────────────

# 旧名 file_rw → 拆分后的单一用途工具（USER/CONTROLLER 定义存库不随 seed 更新）
_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "file_rw": ("read_file", "list_dir", "write_file", "append_file"),
    # Hermes 方案：slide_generator 演进为 office_pptx（白名单旧名兼容）
    "slide_generator": ("office_pptx",),
    "tool:slide_generator": ("office_pptx",),
}
RENAMED_TOOLS = frozenset({"file_rw", "tool:file_rw"})
_FILE_TOOL_EXAMPLES = (
    '读: read_file {"path": "README.md"}；'
    '列: list_dir {"path": "."}；'
    '写: write_file {"path": "README.md", "content": "..."}；'
    '追加: append_file {"path": "README.md", "content": "..."}'
)


def expand_tool_whitelist(whitelist):
    """白名单别名展开（保持顺序、去重）；None 原样返回。"""
    if whitelist is None:
        return None
    out: list[str] = []
    for item in whitelist:
        bare = str(item).split(":", 1)[-1]
        for name in _TOOL_ALIASES.get(bare, (str(item),)):
            if name not in out:
                out.append(name)
    return out


def tool_error(code: str, error: str, hint: str = "") -> dict:
    """工具失败统一形态（error_code 供审计统计，hint 给模型下一步）。"""
    out = {"ok": False, "error_code": code, "error": error}
    if hint:
        out["hint"] = hint
    return out


def renamed_tool_error(name: str) -> dict:
    return tool_error(
        "TOOL_RENAMED",
        f"工具 {name} 已拆分为 read_file / list_dir / write_file / append_file，不再可用",
        f"请改用新工具。{_FILE_TOOL_EXAMPLES}",
    )


def _data_tasks_root() -> Path:
    return (
        _WORKSPACE_ROOT
        if _WORKSPACE_ROOT.is_absolute()
        else Path("data/tasks").resolve()
    )


def _within(p: Path, root: Path) -> bool:
    try:
        return p.is_relative_to(root)
    except ValueError:
        return False


def resolve_workspace_path(path: str) -> Path:
    """data/tasks 禁闭（防穿越）——相对路径按 cwd 解析。

    根动态计算（cwd/data/tasks）而非模块级固化——
    测试 chdir 隔离后语义一致（生产 cwd 即项目根，行为不变）。
    """
    p = Path(path)
    resolved = (p if p.is_absolute() else Path.cwd() / p).resolve()
    root = _data_tasks_root()
    if not _within(resolved, root):
        raise ValueError(f"路径越界: {path}——只允许 {root} 工作区内")
    return resolved


def resolve_task_path(path: str, ws_root: Path) -> Path:
    """任务级路径解析：相对路径以本任务工作区为根（读/列/写同一语义）。

    'data/tasks/...' 指向别的任务时按 cwd 解析（读上游产物）；
    指向本任务、或把任务前缀重复拼一层（'1/8/pom.xml'、
    'data/tasks/1/8/pom.xml'——实例: 任务8 落盘 data/tasks/1/8/1/8/pom.xml）
    的，剥前缀后落回本任务工作区。不做范围判定——由调用方按读/写分别禁闭。
    """
    norm = str(path).replace("\\", "/")
    if norm.startswith("data/tasks"):
        candidate = (Path.cwd() / norm).resolve()
        if not _within(candidate, ws_root):
            return candidate
    parts = list(Path(norm).parts)
    while parts and parts[0] == ".":
        parts.pop(0)
    if parts[:2] == ["data", "tasks"]:
        parts = parts[2:]
    ws_tail = [str(x) for x in ws_root.parts[-2:]]
    while len(parts) >= 2 and [str(parts[0]), str(parts[1])] == ws_tail:
        parts = parts[2:]
    p = Path(*parts) if parts else Path(".")
    if p.is_absolute():
        return p.resolve()
    if ".." in p.parts:
        return (Path.cwd() / p).resolve()
    return (ws_root / p).resolve()


def _rel_display(p: Path, ws_root: Path | None) -> str:
    if ws_root is not None and _within(p, ws_root):
        rel = p.relative_to(ws_root).as_posix()
        return rel or "."
    return str(p)


class _FileToolBase:
    """文件工具公共部分：注册元信息 + 读/写两种禁闭。"""

    name = ""
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        type(self).capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    @staticmethod
    def _ws_root(payload: dict) -> Path | None:
        raw = payload.get("__ws_root")
        return Path(str(raw)).resolve() if raw else None

    @staticmethod
    def _target(path: str, ws_root: Path | None, *, write: bool):
        """→ (resolved, None) 或 (None, error_dict)。

        读范围: data/tasks 全域（可读上游产物）；写范围: 本任务工作区。
        """
        try:
            if ws_root is None:
                return resolve_workspace_path(path), None
            resolved = resolve_task_path(path, ws_root)
        except ValueError as e:
            return None, tool_error(
                "OUT_OF_SCOPE", str(e),
                "请用相对路径（以本任务工作区为根），如 README.md、src/main.py",
            )
        allowed = ws_root if write else _data_tasks_root()
        if not _within(resolved, allowed):
            scope = "本任务工作区" if write else "data/tasks"
            return None, tool_error(
                "OUT_OF_SCOPE",
                f"路径越界: {path}——只允许{scope}内（{allowed}）",
                "请用相对路径（以本任务工作区为根），如 README.md、"
                "src/main.py、tests/test_x.py；其他任务目录只读",
            )
        return resolved, None


class ReadFileProvider(_FileToolBase):
    """读文件（data/tasks 内）。"""

    name = "tool:read_file"

    async def invoke(self, payload: dict) -> dict:
        path = str(payload.get("path") or "").strip()
        if not path:
            return tool_error(
                "MISSING_FIELD", "read_file 缺少 path",
                '示例: read_file {"path": "README.md"}',
            )
        ws_root = self._ws_root(payload)
        resolved, err = self._target(path, ws_root, write=False)
        if err:
            return err
        if not resolved.is_file():
            return tool_error(
                "NOT_FOUND", f"文件不存在: {path}",
                "用 list_dir 查看目录；要新建文件请用 write_file",
            )
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return tool_error("IO_ERROR", f"读取失败: {e}")
        return {"ok": True, "path": str(resolved),
                "content": content[:_OUTPUT_LIMIT],
                # 分发层取 stdout——模型必须能看到文件内容
                "stdout": content[:_OUTPUT_LIMIT]}


class ListDirProvider(_FileToolBase):
    """列目录；条目为相对本任务工作区的路径（可原样用于读写）。"""

    name = "tool:list_dir"

    async def invoke(self, payload: dict) -> dict:
        ws_root = self._ws_root(payload)
        path = str(payload.get("path") or "").strip()
        if not path:
            path = "." if ws_root is not None else "data/tasks"
        resolved, err = self._target(path, ws_root, write=False)
        if err:
            return err
        if not resolved.is_dir():
            return tool_error(
                "NOT_FOUND", f"目录不存在: {path}",
                '用 list_dir {"path": "."} 查看本任务工作区根目录',
            )
        rel_dir = _rel_display(resolved, ws_root)
        items = []
        for f in sorted(resolved.iterdir())[:100]:
            name = f.name + ("/" if f.is_dir() else "")
            items.append(name if rel_dir in (".", str(resolved))
                         else f"{rel_dir}/{name}")
        stdout = (f"目录 {rel_dir}: " + ", ".join(items[:30])
                  if items else f"目录 {rel_dir}: （空目录）")
        return {"ok": True, "path": str(resolved), "items": items,
                "stdout": stdout}


def _persist_text(provider: _FileToolBase, payload: dict, *, append: bool) -> dict:
    """write_file / append_file 共用落盘（禁闭、冻结脚本、回执）。"""
    tool = "append_file" if append else "write_file"
    path = str(payload.get("path") or "").strip()
    content = payload.get("content")
    missing = [k for k, v in (("path", path), ("content", content))
               if v is None or not str(v).strip()]
    if missing:
        return tool_error(
            "MISSING_FIELD", f"{tool} 缺少 {', '.join(missing)}",
            f'示例: {tool} {{"path": "README.md", "content": "..."}}',
        )
    content = str(content)
    from project_runtime.office.controlled import (
        binary_suffix_denied,
        controlled_suffix_of,
    )
    from project_runtime.procedure.delivery_validator import (
        SCRIPT_NAME,
        is_validator_script_path,
    )
    if is_validator_script_path(path) or path.endswith(SCRIPT_NAME):
        return tool_error(
            "FORBIDDEN_PATH",
            f"禁止写入冻结校验脚本 {SCRIPT_NAME}",
            "请重新「生成计划」更新闸门",
        )
    if controlled_suffix_of(path):
        return binary_suffix_denied(path)
    ws_root = provider._ws_root(payload)
    resolved, err = provider._target(path, ws_root, write=True)
    if err:
        return err
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if append and resolved.exists():
            with resolved.open("a", encoding="utf-8") as fh:
                fh.write(content)
        else:
            resolved.write_text(content, encoding="utf-8")
    except OSError as e:
        return tool_error("IO_ERROR", f"写入失败: {e}")
    nbytes = len(content.encode("utf-8"))
    verb = "已追加" if append else "已写入"
    stdout = (f"{verb} {_rel_display(resolved, ws_root)}: "
              f"{len(content)} 字符（{nbytes} 字节）。")
    if ws_root is not None:
        n_files = sum(1 for f in ws_root.rglob("*") if f.is_file())
        stdout += f"本任务工作区现有 {n_files} 个文件。"
    if payload.get("__repaired"):
        stdout += ("注意：本次参数在传输中被截断后修复，内容可能不完整，"
                   "请用 read_file 检查文件末尾。")
    return {"ok": True, "path": str(resolved), "stdout": stdout,
            "bytes": nbytes}


class WriteFileProvider(_FileToolBase):
    """新建或覆盖文件（仅本任务工作区；无写入配额）。"""

    name = "tool:write_file"

    async def invoke(self, payload: dict) -> dict:
        return _persist_text(self, payload, append=False)


class AppendFileProvider(_FileToolBase):
    """追加写入（文件不存在则创建；禁闭与 write_file 一致）。"""

    name = "tool:append_file"

    async def invoke(self, payload: dict) -> dict:
        return _persist_text(self, payload, append=True)


# ── shell（三防禁闭——§4.1 P2-1 返修条款）─────────────────────────

# 命令白名单: 命令名 → 允许的参数模式（正则；None=任意）
_SHELL_WHITELIST: dict[str, str | None] = {
    "ls": None, "cat": None, "head": None, "tail": None, "dir": None,
    "type": None,  # Windows 映射自 cat
    "findstr": None,  # Windows 映射自 grep
    # python/pytest: 特殊哨兵 PATHTOKENS——token 级校验（见 _path_tokens_ok）
    # （批次1 字符集正则含空格和'-', ' -c evil.cfg' 可注入——批次3 修复;
    #   纯正则负向环视要么误伤 'my-file.py' 要么漏 '-c'——函数才是对的）
    "python": "PATHTOKENS",
    "pip": r"^(list|show|freeze)( .*)?$",
    "pytest": "PATHTOKENS",
    "npm": r"^(run|test|list)( .*)?$",
    "npx": r"^[a-zA-Z0-9@/.:_-]+( .*)?$",
    "git": r"^(status|diff|log|show|branch)( .*)?$",  # 限只读子命令
    "find": None, "grep": None, "wc": None, "echo": None,
}
# 注入黑名单（参数层）
_SHELL_FORBIDDEN = re.compile(
    r"(^|[;&|`$])\s*(rm\s+-rf|del\s+/[sq]|format|reg\s|regedit|"
    r"shutdown|taskkill|cd\s+[A-Za-z]:)",  # cd 绝对路径=工作区逃逸
    re.IGNORECASE,
)
_SHELL_TIMEOUT_S = 120


def _path_tokens_ok(args: str) -> bool:
    """token 级路径校验（python/pytest 哨兵用）。

    每个空格分隔 token 必须是纯路径（拒 - 开头 flag——
    -c/--rootdir 配置注入全灭; 允许 'my-file.py' 带连字符文件名，
    只拒 token 以 '-' 开头的形态）。
    """
    if not args:
        return True
    if args != args.strip() or "  " in args:
        return False
    for tok in args.split(" "):
        if not tok or tok.startswith("-"):
            return False
        if not re.match(r"^[a-zA-Z0-9_/.:\\-]+$", tok):
            return False
    return True


_WIN_TEXT_CMDS = frozenset({"head", "tail", "wc"})


def translate_win_shell(cmd_name: str, args: str) -> tuple[str, str]:
    """SYS-160: Unix 命令 → Windows 等价。不识别则原样返回。"""
    if cmd_name == "ls":
        rest = " ".join(t for t in args.split() if not t.startswith("-"))
        return "dir", rest
    if cmd_name == "cat":
        rest = " ".join(t for t in args.split() if not t.startswith("-"))
        return "type", rest
    if cmd_name == "grep":
        return _translate_grep(args)
    if cmd_name == "find":
        return _translate_find(args)
    return cmd_name, args


def _translate_grep(args: str) -> tuple[str, str]:
    flags: list[str] = []
    rest: list[str] = []
    toks = args.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-i", "--ignore-case"):
            flags.append("/I")
        elif t in ("-n", "--line-number"):
            flags.append("/N")
        elif t.startswith("-"):
            i += 1
            continue
        else:
            rest.append(t)
        i += 1
    if not rest:
        return "findstr", " ".join(flags)
    pattern = rest[0].replace('"', "")
    files = rest[1:] or ["*"]
    return "findstr", " ".join([*flags, f'/C:"{pattern}"', *files])


def _translate_find(args: str) -> tuple[str, str]:
    toks = args.split()
    name_pat = None
    roots: list[str] = []
    unixish = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-name", "-iname") and i + 1 < len(toks):
            unixish = True
            name_pat = toks[i + 1].strip("'\"")
            i += 2
            continue
        if t.startswith("-"):
            unixish = True
            i += 1
            continue
        roots.append(t)
        i += 1
    if not unixish:
        return "find", args
    root = roots[0] if roots else "."
    target = str(Path(root) / name_pat) if name_pat else root
    return "dir", f"/s /b {target}"


def _safe_under(cwd: Path, rel: str) -> Path | None:
    try:
        p = (cwd / rel).resolve()
        p.relative_to(cwd.resolve())
        return p
    except (ValueError, OSError):
        return None


def _parse_n_and_path(args: str, default_n: int) -> tuple[int, str] | None:
    toks = args.split()
    n = default_n
    files: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-n", "--lines", "-c", "--bytes") and i + 1 < len(toks):
            try:
                n = int(toks[i + 1])
            except ValueError:
                return None
            i += 2
            continue
        if re.fullmatch(r"-\d+", t):
            n = int(t[1:])
            i += 1
            continue
        if t.startswith("-"):
            i += 1
            continue
        files.append(t)
        i += 1
    if not files:
        return None
    return n, files[0]


def run_win_text_cmd(cmd_name: str, args: str, cwd: Path) -> dict:
    """Windows 上用纯 Python 实现 head/tail/wc，避免 Unix 二进制缺失。"""
    if cmd_name in ("head", "tail"):
        parsed = _parse_n_and_path(args, 10)
        if parsed is None:
            return tool_error(
                "INVALID_ARGS",
                f"{cmd_name} 参数无法解析",
                f"示例: {cmd_name} -n 20 file.txt",
            )
        n, rel = parsed
        path = _safe_under(cwd, rel)
        if path is None or not path.is_file():
            return tool_error("NOT_FOUND", f"文件不存在: {rel}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True,
        )
        out = "".join(lines[:n] if cmd_name == "head" else lines[-n:])
        return {"ok": True, "code": 0, "stdout": out[:_OUTPUT_LIMIT], "stderr": ""}

    toks = args.split()
    mode = "all"
    files: list[str] = []
    for t in toks:
        if t in ("-l", "--lines"):
            mode = "l"
        elif t in ("-w", "--words"):
            mode = "w"
        elif t in ("-c", "--bytes"):
            mode = "c"
        elif t.startswith("-"):
            continue
        else:
            files.append(t)
    if not files:
        return tool_error(
            "INVALID_ARGS", "wc 需要文件路径", "示例: wc -l file.txt",
        )
    path = _safe_under(cwd, files[0])
    if path is None or not path.is_file():
        return tool_error("NOT_FOUND", f"文件不存在: {files[0]}")
    text = path.read_text(encoding="utf-8", errors="replace")
    n_lines = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
    n_words = len(text.split())
    n_bytes = path.stat().st_size
    if mode == "l":
        out = str(n_lines)
    elif mode == "w":
        out = str(n_words)
    elif mode == "c":
        out = str(n_bytes)
    else:
        out = f"{n_lines} {n_words} {n_bytes}"
    return {"ok": True, "code": 0, "stdout": out[:_OUTPUT_LIMIT], "stderr": ""}


_REDIR_TARGET = re.compile(r"(>>?)\s*[\"']?([^\s\"'|&;]+)")


def _deny_controlled_redirect(command: str) -> dict | None:
    """A1b: echo/cmd 重定向到受控后缀则拒绝。"""
    from project_runtime.office.controlled import controlled_suffix_of

    for m in _REDIR_TARGET.finditer(command):
        target = m.group(2)
        if controlled_suffix_of(target):
            return tool_error(
                "BINARY_SUFFIX_DENIED",
                f"禁止重定向写入受控后缀: {target}",
                "请改用 office_* / image_raster / arch_diagram 生成",
            )
    return None


def _snapshot_controlled(cwd: Path) -> dict[str, tuple[int, int, bytes | None]]:
    """path → (size, mtime_ns, bytes|None)。小文件保留字节以便还原。"""
    from project_runtime.office.controlled import controlled_suffix_of

    snap: dict[str, tuple[int, int, bytes | None]] = {}
    if not cwd.exists():
        return snap
    for p in cwd.rglob("*"):
        if not p.is_file() or not controlled_suffix_of(p.name):
            continue
        try:
            st = p.stat()
            data = p.read_bytes() if st.st_size <= 2_000_000 else None
            snap[str(p.relative_to(cwd))] = (
                st.st_size, st.st_mtime_ns, data,
            )
        except OSError:
            continue
    return snap


def _restore_controlled(
    cwd: Path, snap: dict[str, tuple[int, int, bytes | None]],
) -> list[str]:
    """还原新建/变更的受控后缀文件；返回被还原的相对路径。"""
    from project_runtime.office.controlled import controlled_suffix_of

    restored: list[str] = []
    current: set[str] = set()
    if cwd.exists():
        for p in cwd.rglob("*"):
            if p.is_file() and controlled_suffix_of(p.name):
                try:
                    current.add(str(p.relative_to(cwd)))
                except ValueError:
                    continue
    # 新建 → 删除
    for rel in current - set(snap):
        try:
            (cwd / rel).unlink(missing_ok=True)
            restored.append(rel)
        except OSError:
            pass
    # 变更 → 写回
    for rel, (size, mtime_ns, data) in snap.items():
        p = cwd / rel
        try:
            if not p.exists():
                if data is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                    restored.append(rel)
                continue
            st = p.stat()
            if st.st_size != size or st.st_mtime_ns != mtime_ns:
                if data is not None:
                    p.write_bytes(data)
                    restored.append(rel)
        except OSError:
            continue
    return restored


class ShellProvider:
    """受限 shell——三防: 命令白名单+参数模式 / cwd 硬锁 / 超时截断。"""

    name = "tool:shell"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        ShellProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        command = str(payload.get("command") or "").strip()
        if not command:
            return {"ok": False, "error": "command 必填"}
        # 工作区（cwd 硬锁——§4.1: 不锁 cwd 等于裸奔数据库文件）
        workspace = payload.get("workspace")  # data/tasks/{pid}/{tid}
        if workspace:
            try:
                cwd = resolve_workspace_path(str(workspace))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            if not cwd.is_dir():
                cwd.mkdir(parents=True, exist_ok=True)
        else:
            cwd = Path.cwd()

        # ① 命令白名单 + 参数模式
        parts = command.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        # SYS-160: Windows 把常见 Unix 命令映射到等价命令，或内建实现
        win_text = False
        if os.name == "nt":
            if cmd_name in _WIN_TEXT_CMDS:
                win_text = True
            else:
                mapped_name, mapped_args = translate_win_shell(cmd_name, args)
                if mapped_name != cmd_name or mapped_args != args:
                    cmd_name = mapped_name
                    args = mapped_args
                    command = (
                        mapped_name + ((" " + mapped_args) if mapped_args else "")
                    )
        # 返修V3 批C: skill 固定脚本绝对路径白名单（仍过 PATHTOKENS）
        skill_script_ok = False
        if cmd_name in ("python", "python3"):
            try:
                from project_runtime.skills.install_gates import (
                    is_whitelisted_skill_script,
                )
                skill_script_ok = is_whitelisted_skill_script(command)
            except Exception:  # noqa: BLE001
                skill_script_ok = False
        if cmd_name not in _SHELL_WHITELIST and not skill_script_ok:
            return tool_error(
                "NOT_WHITELISTED",
                f"命令不在白名单: {cmd_name}（允许: "
                f"{sorted(_SHELL_WHITELIST)[:8]}…）",
            )
        pattern = _SHELL_WHITELIST.get(cmd_name)
        if skill_script_ok:
            # 白名单脚本: 第一参为绝对路径，其后仍禁 -flag
            rest = " ".join(command.split()[2:])
            if rest and not _path_tokens_ok(rest):
                return {"ok": False,
                        "error": f"skill 脚本参数禁 -flag: {rest[:60]}"}
        elif pattern == "PATHTOKENS":
            if not _path_tokens_ok(args):
                return {"ok": False,
                        "error": f"参数模式不合规（只准纯路径，禁 -flag）: {args[:60]}"}
        elif pattern and not re.match(pattern, args):
            return {"ok": False,
                    "error": f"参数模式不合规（该命令限安全参数）: {args[:60]}"}
        # ② 注入黑名单（cd 绝对路径/危险命令）
        if _SHELL_FORBIDDEN.search(command):
            return {"ok": False, "error": "命令含禁用模式（工作区逃逸/危险操作）"}
        if win_text:
            return run_win_text_cmd(cmd_name, args, cwd)

        # A1b: 重定向写入受控二进制后缀 → 拒绝
        redir_err = _deny_controlled_redirect(command)
        if redir_err is not None:
            return redir_err

        # A1b: 非 skill 的 python 写受控后缀 → 执行前后摘要还原
        snap = None
        if cmd_name in ("python", "python3") and not skill_script_ok:
            snap = _snapshot_controlled(cwd)

        # ③ 受限执行
        import subprocess

        def _blocking() -> subprocess.CompletedProcess:
            return subprocess.run(  # noqa: S603
                command, shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=_SHELL_TIMEOUT_S,
            )

        try:
            proc = await asyncio.wait_for(
                asyncio.to_thread(_blocking), timeout=_SHELL_TIMEOUT_S + 10
            )
        except subprocess.TimeoutExpired:
            if snap is not None:
                _restore_controlled(cwd, snap)
            return {"ok": False, "error": f"超时（>{_SHELL_TIMEOUT_S}s）"}
        except Exception as e:  # noqa: BLE001
            if snap is not None:
                _restore_controlled(cwd, snap)
            return {"ok": False, "error": f"执行失败: {e}"}
        if snap is not None:
            restored = _restore_controlled(cwd, snap)
            if restored:
                return tool_error(
                    "BINARY_WRITE_ESCAPE",
                    "非 skill 的 python 改写了受控二进制后缀，已还原："
                    + ", ".join(restored[:8]),
                    "请改用 office_pptx / office_docx / office_xlsx / "
                    "office_pdf / image_raster / arch_diagram",
                )
        out = (proc.stdout or "")[:_OUTPUT_LIMIT]
        err = (proc.stderr or "")[:2000]
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "stdout": out, "stderr": err}


# ── code_search（批次 1 简版: glob+grep 扫描；ripgrep 批次 3）─────


class CodeSearchProvider:
    """代码检索——pattern（正则）+ glob（文件名过滤）+ 工作区范围。"""

    name = "tool:code_search"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        CodeSearchProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        pattern = str(payload.get("pattern") or "").strip()
        glob = str(payload.get("glob") or "*.py")
        scope = payload.get("scope") or "data/tasks"
        if not pattern:
            return {"ok": False, "error": "pattern 必填"}
        try:
            root = resolve_workspace_path(str(scope))
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        def _blocking() -> list[dict]:
            hits: list[dict] = []
            rx = re.compile(pattern)
            for f in root.rglob(glob):
                if not f.is_file() or len(hits) >= 50:
                    break
                try:
                    for ln, line in enumerate(
                        f.read_text(encoding="utf-8", errors="replace")
                        .splitlines(), 1
                    ):
                        if rx.search(line):
                            hits.append({
                                "file": str(f), "line": ln,
                                "text": line.strip()[:200],
                            })
                            if len(hits) >= 50:
                                break
                except OSError:
                    continue
            return hits

        try:
            hits = await asyncio.to_thread(_blocking)
        except re.error as e:
            return {"ok": False, "error": f"正则无效: {e}"}
        return {"ok": True, "count": len(hits), "hits": hits[:50]}


# slide_generator → office_pptx：见 project_runtime.office.providers


# ── 注册（组合根——与 search providers 同模式）─────────────────────


def register_worker_tools(registry) -> None:
    """把批次 1 五插件注册进共享 registry（幂等覆盖）。

    搜索 provider（web_search）在 research.providers 注册——本函数只管 TOOL 面。
    """
    registry.register(WebFetchProvider())
    registry.register(ReadFileProvider())
    registry.register(ListDirProvider())
    registry.register(WriteFileProvider())
    registry.register(AppendFileProvider())
    registry.register(ShellProvider())
    registry.register(CodeSearchProvider())
    from project_runtime.office.providers import register_office_tools

    register_office_tools(registry)
