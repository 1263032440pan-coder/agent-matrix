"""Worker 底座插件（20号规范 §4.1——批次 1 五件）.

全部 TOOL 能力 Provider（D16 契约），注册进 shared_registry，
经 Worker 工具循环由模型自决调用（描述即调用策略——§4.3）。

- webfetch:        拉取指定 URL（markdown/text）
- file_rw:         产物读写（data/tasks/ 工作区禁闭——防穿越）
- shell:           白名单受限执行（cwd 硬锁任务工作区 + 参数模式白名单）
- code_search:     代码检索（文件名 glob + 内容 grep 简版，ripgrep 批次 3）
- slide_generator: md → .pptx（python-pptx——批次 1 刚需）
"""

from __future__ import annotations

import asyncio
import logging
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


# ── file_rw（工作区禁闭）───────────────────────────────────────────


class FileRwProvider:
    """产物读写——限定 data/tasks/{project}/{task}/ 工作区（防穿越）。

    读: 任意工作区内文件；写: 调用方给定的任务工作区。
    """

    name = "tool:file_rw"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        FileRwProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        action = str(payload.get("action") or "read")
        path = str(payload.get("path") or "")
        if not path:
            return {"ok": False, "error": "path 必填"}
        try:
            resolved = self._resolve(path)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if action == "read":
            if not resolved.is_file():
                return {"ok": False, "error": f"文件不存在: {path}"}
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return {"ok": False, "error": f"读取失败: {e}"}
            return {"ok": True, "path": str(resolved),
                    "content": content[:_OUTPUT_LIMIT]}
        if action == "list":
            if not resolved.is_dir():
                return {"ok": False, "error": f"目录不存在: {path}"}
            items = [
                f.name + ("/" if f.is_dir() else "")
                for f in sorted(resolved.iterdir())
            ][:100]
            return {"ok": True, "path": str(resolved), "items": items}
        return {"ok": False, "error": f"未知 action: {action}（批次 1 只读）"}

    @staticmethod
    def _resolve(path: str) -> Path:
        """工作区禁闭——解析后必须落在 data/tasks/ 下（防穿越）。

        根动态计算（cwd/data/tasks）而非模块级固化——
        测试 chdir 隔离后语义一致（生产 cwd 即项目根，行为不变）。
        """
        p = Path(path)
        resolved = (p if p.is_absolute() else Path.cwd() / p).resolve()
        # 根: 测试可注入 _WORKSPACE_ROOT; 未注入则 cwd 动态计算
        root = (
            _WORKSPACE_ROOT
            if _WORKSPACE_ROOT.is_absolute()
            else Path("data/tasks").resolve()
        )
        if not str(resolved).startswith(str(root)):
            raise ValueError(
                f"路径越界: {path}——只允许 {root} 工作区内"
            )
        return resolved


# ── shell（三防禁闭——§4.1 P2-1 返修条款）─────────────────────────

# 命令白名单: 命令名 → 允许的参数模式（正则；None=任意）
_SHELL_WHITELIST: dict[str, str | None] = {
    "ls": None, "cat": None, "head": None, "tail": None, "dir": None,
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


class ShellProvider:
    """受限 shell——三防: 命令白名单+参数模式 / cwd 硬锁 / 超时截断。"""

    name = "tool:shell"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

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
                cwd = FileRwProvider._resolve(str(workspace))
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
        if cmd_name not in _SHELL_WHITELIST:
            return {"ok": False,
                    "error": f"命令不在白名单: {cmd_name}（允许: "
                             f"{sorted(_SHELL_WHITELIST)[:8]}…）"}
        pattern = _SHELL_WHITELIST[cmd_name]
        if pattern == "PATHTOKENS":
            if not _path_tokens_ok(args):
                return {"ok": False,
                        "error": f"参数模式不合规（只准纯路径，禁 -flag）: {args[:60]}"}
        elif pattern and not re.match(pattern, args):
            return {"ok": False,
                    "error": f"参数模式不合规（该命令限安全参数）: {args[:60]}"}
        # ② 注入黑名单（cd 绝对路径/危险命令）
        if _SHELL_FORBIDDEN.search(command):
            return {"ok": False, "error": "命令含禁用模式（工作区逃逸/危险操作）"}

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
            return {"ok": False, "error": f"超时（>{_SHELL_TIMEOUT_S}s）"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"执行失败: {e}"}
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
            root = FileRwProvider._resolve(str(scope))
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


# ── slide_generator（python-pptx——批次 1 刚需）─────────────────────


class SlideGeneratorProvider:
    """md → .pptx（基础版式: 标题页/目录/内容页/要点页/结尾）。

    输入 markdown 约定:
      # 一级标题        → 演示文稿标题（首页）
      ## 二级标题       → 每节一页（页标题）
      - 要点            → 要点列表（bullet）
      普通段落          → 内容文本框
    产物落 Artifact（调用方经 file 管理器登记——本 provider 返回写盘路径）。
    """

    name = "tool:slide_generator"
    capability = None
    permission = "READ_ONLY"
    priority = 150
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        SlideGeneratorProvider.capability = CapabilityType.TOOL

    async def health(self) -> bool:
        try:
            import pptx  # noqa: F401

            return True
        except ImportError:
            return False

    async def invoke(self, payload: dict) -> dict:
        markdown = str(payload.get("markdown") or "")
        workspace = payload.get("workspace") or "."
        filename = str(payload.get("filename") or "slides.pptx")
        if not markdown.strip():
            return {"ok": False, "error": "markdown 必填"}
        try:
            out_dir = FileRwProvider._resolve(str(workspace))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / re.sub(r'[<>:"/\\|?*]', "_", filename)

        def _blocking() -> int:
            from pptx import Presentation
            from pptx.util import Pt

            prs = Presentation()
            title_slide_added = False
            current_title = None
            bullets: list[str] = []

            def flush_bullets():
                nonlocal bullets
                if current_title and bullets:
                    slide = prs.slides.add_slide(prs.slide_layouts[1])
                    slide.shapes.title.text = current_title
                    body = slide.placeholders[1]
                    tf = body.text_frame
                    for i, b in enumerate(bullets[:8]):
                        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                        p.text = b
                        p.font.size = Pt(16)
                    bullets = []

            for line in markdown.splitlines():
                stripped = line.strip()
                if stripped.startswith("# ") and not stripped.startswith("## "):
                    # 首页标题
                    slide = prs.slides.add_slide(prs.slide_layouts[0])
                    slide.shapes.title.text = stripped[2:]
                    subtitle = slide.placeholders[1]
                    subtitle.text = " autogenerated · Project Autonomous Agent"
                    title_slide_added = True
                elif stripped.startswith("## "):
                    flush_bullets()
                    current_title = stripped[3:]
                    slide = prs.slides.add_slide(prs.slide_layouts[1])
                    slide.shapes.title.text = current_title
                elif stripped.startswith(("- ", "* ")):
                    bullets.append(stripped[2:])
                elif stripped and current_title:
                    flush_bullets()
                    slide = prs.slides.add_slide(prs.slide_layouts[1])
                    slide.shapes.title.text = current_title
                    body = slide.placeholders[1]
                    body.text_frame.text = stripped[:500]
            flush_bullets()
            if not title_slide_added and prs.slides:
                pass  # 无 # 标题也允许（首节页即首页）
            count = len(prs.slides._sldIdLst)  # noqa: SLF001 — pptx 内部
            prs.save(str(out_path))
            return count

        try:
            count = await asyncio.to_thread(_blocking)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"生成失败: {e}"}
        return {"ok": True, "path": str(out_path), "slides": count}


# ── 注册（组合根——与 search providers 同模式）─────────────────────


def register_worker_tools(registry) -> None:
    """把批次 1 五插件注册进共享 registry（幂等覆盖）。

    搜索 provider（web_search）在 research.providers 注册——本函数只管 TOOL 面。
    """
    registry.register(WebFetchProvider())
    registry.register(FileRwProvider())
    registry.register(ShellProvider())
    registry.register(CodeSearchProvider())
    registry.register(SlideGeneratorProvider())
