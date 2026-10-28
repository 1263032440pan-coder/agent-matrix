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

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

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
        # V1.6 序0: write 的禁闭判定在 write 分支内自管（__ws_root 任务级
        # 优先, 相对路径如 src/main.py 合法）——开头 resolve 只服务 read/list
        if action != "write":
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
                    "content": content[:_OUTPUT_LIMIT],
                    # 分发层取 stdout——模型必须能看到文件内容
                    "stdout": content[:_OUTPUT_LIMIT]}
        if action == "list":
            if not resolved.is_dir():
                return {"ok": False, "error": f"目录不存在: {path}"}
            items = [
                f.name + ("/" if f.is_dir() else "")
                for f in sorted(resolved.iterdir())
            ][:100]
            return {"ok": True, "path": str(resolved), "items": items,
                    "stdout": "目录 " + resolved.name + "/: " +
                            ", ".join(items[:30]) if items else "（空目录）"}
        if action == "write":
            content = payload.get("content")
            if content is None or not str(content).strip():
                return {"ok": False, "error": "write 需要 content"}
            content = str(content)
            # V1.6d: 禁止改写项目级冻结校验脚本
            from project_runtime.procedure.delivery_validator import (
                SCRIPT_NAME,
                is_validator_script_path,
            )
            if is_validator_script_path(path) or str(path).endswith(
                SCRIPT_NAME
            ):
                return {
                    "ok": False,
                    "error": (
                        f"禁止写入冻结校验脚本 {SCRIPT_NAME}——"
                        "请重新「生成计划」更新闸门"
                    ),
                }
            # V1.6 序0: 任务级写禁闭——调用方注入 __ws_root 时,
            # resolved 必须落在该任务工作区内（防写到相邻目录逃逸 harvest）
            ws_root_raw = payload.get("__ws_root")
            try:
                if ws_root_raw:
                    ws_root = Path(str(ws_root_raw)).resolve()
                    # 相对路径语义: src/main.py → 拼进任务工作区;
                    # 但带 ../ 或 data/tasks 前缀的（试图指别的目录）
                    # 按 cwd 解析再禁闭——两种都逃不出 startswith 检查
                    # 返修单v2 P4: 剥离路径中重复的任务前缀（'1/8/pom.xml'
                    # / 'data/tasks/1/8/pom.xml' 这类把任务路径再拼一层
                    # 的形态——实例: 任务8 落盘 data/tasks/1/8/1/8/pom.xml）
                    p_ = Path(str(path).replace(chr(92), "/"))
                    _parts = list(p_.parts)
                    while _parts and _parts[0] in (".",):
                        _parts.pop(0)
                    if _parts[:2] == ["data", "tasks"]:
                        _parts = _parts[2:]
                    _ws_tail = [str(x) for x in ws_root.parts[-2:]]
                    while (
                        len(_parts) >= 2
                        and [str(_parts[0]), str(_parts[1])] == _ws_tail
                    ):
                        _parts = _parts[2:]
                    p_ = Path(*_parts) if _parts else Path(path)
                    # 形态4 修正: 'data/tasks' 前缀先做域内检查——
                    # 剥前缀只对【本任务自己的】路径合法化, 别的任务的
                    # data/tasks/{pid}/{tid} 保持越界判定
                    _norm = str(path).replace(chr(92), "/")
                    if _norm.startswith("data/tasks"):
                        _candidate = (Path.cwd() / _norm).resolve()
                        _in_ws = str(_candidate).startswith(str(ws_root))
                        if not _in_ws:
                            # 不在本任务工作区 → 保持原路径按 cwd 判界
                            resolved = _candidate
                            if not str(resolved).startswith(str(ws_root)):
                                return {
                                    "ok": False,
                                    "error": (
                                        f"路径越界: {path}——本任务只允许"
                                        f"写入 {ws_root} 内。请用相对路径如"
                                        f" src/main.py、tests/test_x.py、"
                                        f"README.md"
                                    ),
                                }
                    if p_.is_absolute():
                        resolved = p_.resolve()
                    elif '..' in p_.parts or str(p_).replace(
                        chr(92), '/'
                    ).startswith('data/tasks'):
                        resolved = (Path.cwd() / p_).resolve()
                    else:
                        resolved = (ws_root / p_).resolve()
                    if not str(resolved).startswith(str(ws_root)):
                        return {
                            "ok": False,
                            "error": (
                                f"路径越界: {path}——本任务只允许写入 "
                                f"{ws_root} 内。请用相对路径如 "
                                f"src/main.py、tests/test_x.py、README.md"
                            ),
                        }
                else:
                    resolved = self._resolve(path)
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content, encoding="utf-8")
            except (OSError, ValueError) as e:
                return {"ok": False, "error": f"写入失败: {e}"}
            return {
                "ok": True, "path": str(resolved),
                # 确认文本——工具循环喂回模型（可见自己写了什么）
                "stdout": (
                    f"已写入 {resolved.name}: {len(content)} 字符。"
                ),
                "bytes": len(content.encode("utf-8")),
            }
        return {"ok": False, "error": f"未知 action: {action}"}

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
            return {"ok": False,
                    "error": f"命令不在白名单: {cmd_name}（允许: "
                             f"{sorted(_SHELL_WHITELIST)[:8]}…）"}
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

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_schemas import tool_schema_for

        return tool_schema_for(self.name)

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
        # 返修单-slide_generator落点逃逸: 任务级禁闭——__ws_root 注入时
        # 输出目录强制= 任务工作区, workspace 参数被忽略（与 file_rw 同构;
        # 实测 5 个 pptx 散落工位外, harvest 收割不到 → Gate 误判死循环）
        ws_root_raw = payload.get("__ws_root")
        if ws_root_raw:
            out_dir = Path(str(ws_root_raw)).resolve()
        else:
            try:
                out_dir = FileRwProvider._resolve(str(workspace))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / re.sub(r'[<>:"/\\|?*]', "_", filename)

        def _blocking() -> tuple[int, int]:
            """返修单 V3 批D-D1: 按节建页（对齐 md2pptx）。

            建页单位=节（`##`）而非行；段落/表格/代码归并本页；
            `#` 封面；`---` 显式分页；fence 行不建页。
            返回 (slide_count, h2_section_count)。
            """
            from pptx import Presentation
            from pptx.util import Pt

            prs = Presentation()
            # 默认模板 layout[1]=标题+内容（官方 Title and Content）
            content_layout = prs.slide_layouts[1]

            h2_count = 0
            title_slide_added = False
            current_title: str | None = None
            body_lines: list[str] = []
            in_fence = False

            def flush_section(*, keep_title: str | None = None) -> None:
                """把当前节刷成一页；keep_title 用于 --- 后续页无新 ##。"""
                nonlocal current_title, body_lines
                title = current_title
                if title is None and not body_lines:
                    return
                slide = prs.slides.add_slide(content_layout)
                if title and hasattr(slide.shapes, "title") and slide.shapes.title:
                    slide.shapes.title.text = title
                # 优先 placeholders[1]；否则跳过正文
                body_ph = None
                try:
                    body_ph = slide.placeholders[1]
                except (KeyError, IndexError):
                    for sh in slide.shapes:
                        if sh.has_text_frame and sh != getattr(
                            slide.shapes, "title", None
                        ):
                            body_ph = sh
                            break
                if body_ph is not None and body_lines:
                    tf = body_ph.text_frame
                    tf.clear()
                    for i, line in enumerate(body_lines[:24]):
                        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                        text = line
                        if text.startswith(("- ", "* ")):
                            text = text[2:]
                            p.level = 0
                        p.text = text[:500]
                        p.font.size = Pt(16)
                elif title and body_ph is not None and not body_lines:
                    # 仅标题也算非空页（标题本身有字）
                    pass
                body_lines = []
                current_title = keep_title

            for raw in markdown.splitlines():
                stripped = raw.strip()
                # fenced code: 整块归并，fence 行本身不建页
                if stripped.startswith("```"):
                    in_fence = not in_fence
                    if current_title is not None or body_lines:
                        body_lines.append(stripped[:200])
                    continue
                if in_fence:
                    if current_title is not None or body_lines or title_slide_added:
                        body_lines.append(stripped[:500] if stripped else "")
                    continue

                if stripped == "---":
                    # 显式分页（Marp/md2pptx 语义）
                    flush_section(keep_title=None)
                    continue

                if stripped.startswith("# ") and not stripped.startswith("## "):
                    flush_section()
                    slide = prs.slides.add_slide(prs.slide_layouts[0])
                    slide.shapes.title.text = stripped[2:].strip()
                    try:
                        slide.placeholders[1].text = (
                            "autogenerated · Project Autonomous Agent"
                        )
                    except (KeyError, IndexError):
                        pass
                    title_slide_added = True
                    current_title = None
                    body_lines = []
                    continue

                if stripped.startswith("## "):
                    flush_section()
                    h2_count += 1
                    current_title = stripped[3:].strip()
                    body_lines = []
                    continue

                if not stripped:
                    continue

                # 表格行 / 要点 / 普通段落 → 归并进当前节
                if current_title is None and not title_slide_added:
                    # 无封面无节时，隐式开一节
                    current_title = "内容"
                    h2_count += 1
                if current_title is None and title_slide_added:
                    # 封面后、--- 后的孤儿正文：开无标题续页
                    current_title = ""
                body_lines.append(stripped[:500])

            flush_section()
            if not title_slide_added and len(prs.slides._sldIdLst) == 0:  # noqa: SLF001
                slide = prs.slides.add_slide(prs.slide_layouts[0])
                slide.shapes.title.text = "演示文稿"
            count = len(prs.slides._sldIdLst)  # noqa: SLF001
            prs.save(str(out_path))
            return count, h2_count

        def _meta() -> dict:
            """V1.6 序4: 读回 pptx 元数据（事实返回——判定归模型/skill）。"""
            from pptx import Presentation as _Pres

            m = _Pres(str(out_path))
            slides = list(m.slides)
            non_empty = sum(
                1 for sl in slides
                if any(sh.has_text_frame and sh.text_frame.text.strip()
                       for sh in sl.shapes)
            )
            has_title = bool(
                slides and any(
                    sh.has_text_frame and sh.text_frame.text.strip()
                    for sh in slides[0].shapes
                )
            )
            return {"slides": len(slides),
                    "non_empty_slides": non_empty,
                    "has_title_text": has_title}

        try:
            _count, h2_count = await asyncio.to_thread(_blocking)
            meta = await asyncio.to_thread(_meta)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"生成失败: {e}"}
        # V1.6 序4 + 批D-D3: 元数据 + 页数偏斜归因（事实陈述，判定归模型）
        stdout = (
            f"已生成 {out_path.name}: {meta['slides']} 页"
            f"（{meta['non_empty_slides']} 页有内容）——"
            "请对照交付基线自查页数与内容要求"
        )
        if h2_count > 0 and meta["slides"] > 2 * h2_count:
            stdout += (
                "；可能未按一节一页格式，建议重生成"
                f"（二级标题 {h2_count} vs 实际 {meta['slides']} 页）"
            )
        return {
            "ok": True, "path": str(out_path),
            "slides": meta["slides"],
            "non_empty_slides": meta["non_empty_slides"],
            "has_title_text": meta["has_title_text"],
            "stdout": stdout,
        }


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
