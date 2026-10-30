"""SYS-152～157: 令牌可读化、缺口回执、返工 nudge、连续失败诊断。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_runtime.procedure.token_stop import ObligationStopResult


def describe_obligation_tokens(tokens: list[dict[str, Any]] | None) -> list[str]:
    """把机器令牌翻成带示例路径的人类可读清单。"""
    lines: list[str] = []
    seen: set[str] = set()
    for t in tokens or []:
        tid = str((t or {}).get("id") or "")
        line = ""
        if tid.startswith("prefix:"):
            lab = str((t or {}).get("label") or tid.split(":")[-1]).rstrip("*")
            line = (
                f"根目录需有以 {lab} 开头的文件"
                f"（示例路径：{lab.upper()}.md / {lab}.md）"
            )
        elif tid.startswith("ext:"):
            parts = tid.split(":", 2)
            e = parts[2] if len(parts) >= 3 else ""
            if e == "any":
                exts = [str(x) for x in ((t or {}).get("exts") or [])]
                hint = ",".join(exts) if exts else ".py/.java/.md"
                example = "src/main/java/App.java" if ".java" in hint else "src/main.py"
                line = f"至少 1 个源文件，扩展名属于 {hint}（示例：{example}）"
            elif e:
                ext = e if e.startswith(".") else f".{e}"
                line = f"至少 1 个 {ext} 文件（示例：src/main{ext}）"
        elif tid.startswith("count:"):
            q = int((t or {}).get("quota") or 1)
            line = f"本任务工作区至少 {q} 个常规文件"
        elif tid.startswith("slot:"):
            lab = str((t or {}).get("label") or tid)
            line = f"槽目录有文件：{lab}"
        elif tid.startswith("name:"):
            lab = str((t or {}).get("label") or tid.split(":")[-1])
            line = f"需要名为 {lab} 的文件"
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def planned_paths_cover(
    paths: list[str],
    tokens: list[dict[str, Any]] | None,
) -> ObligationStopResult:
    """把计划中的相对路径当成虚拟文件，离线校验令牌覆盖。"""
    import tempfile

    from project_runtime.procedure.token_stop import (
        check_obligation_tokens_detailed,
    )

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for rel in paths:
            p = root / str(rel).replace("\\", "/").lstrip("/")
            if ".." in p.parts:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("x", encoding="utf-8")
        return check_obligation_tokens_detailed(root, tokens)


def format_gap_receipt(result: ObligationStopResult) -> str:
    """写入成功后附在工具回执末尾。"""
    if result.ok():
        return "义务令牌已全部满足，可以收工。"
    bits: list[str] = []
    for u in result.unmet:
        tid = str(u.get("token_id") or "")
        hint = str(u.get("hint") or u.get("token_id") or "")
        if tid.startswith("prefix:"):
            lab = tid.split(":")[-1].rstrip("*") or "readme"
            bits.append(f"{lab}*（建议路径 {lab.upper()}.md）")
        elif tid.startswith("ext:"):
            bits.append(hint.replace("令牌缺扩展名文件（需其一）：", "扩展名需其一："))
        else:
            bits.append(hint)
    return "剩余缺口：" + "；".join(bits or result.problems)


def workspace_tree(root: Path | str, *, limit: int = 100) -> str:
    """相对路径目录树（最多 limit 项）。"""
    base = Path(root)
    if not base.exists():
        return "（工作区为空）"
    items: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(base).as_posix()
        except ValueError:
            rel = p.as_posix()
        items.append(rel)
        if len(items) >= limit:
            items.append("…")
            break
    return "\n".join(items) if items else "（工作区为空）"


def repair_nudge(
    used: int,
    problems: list[str],
    *,
    error_stats: dict[str, int] | None = None,
    suggested_calls: list[str] | None = None,
) -> str:
    """第 used 次（0-based）返工文案。"""
    head = (
        "【收工校验未通过——须返工】"
        f"（第 {used + 1}/3 次）：\n"
        + "\n".join(f"- {p}" for p in problems)
    )
    stats = error_stats or {}
    calls = suggested_calls or _default_calls(problems)
    if used <= 0:
        extra = "\n请用 write_file 按上列路径补齐，齐了再输出最终交付。不要只口头承诺。"
    elif used == 1:
        stat_txt = "、".join(f"{k}×{v}" for k, v in stats.items()) or "无"
        extra = (
            f"\n本轮工具错误码统计：{stat_txt}。"
            "这是参数问题，不是配额或权限问题。"
            "\n示例：write_file {\"path\": \"README.md\", \"content\": \"# 项目\"}"
            "\n请立即用工具补齐，不要只口头承诺。"
        )
    else:
        extra = (
            "\n仅需补齐以下文件即可收工（最小动作清单）：\n"
            + "\n".join(f"- {c}" for c in calls)
            + "\n请立即调用工具落盘，不要只口头承诺。"
        )
    return head + extra


def _default_calls(problems: list[str]) -> list[str]:
    calls: list[str] = []
    joined = "\n".join(problems).lower()
    if "readme" in joined:
        calls.append(
            'write_file {"path": "README.md", "content": "# 项目说明"}'
        )
    if ".java" in joined or "java" in joined:
        calls.append(
            'write_file {"path": "src/main/java/App.java",'
            ' "content": "public class App {}"}'
        )
    if not calls:
        calls.append(
            'write_file {"path": "README.md", "content": "# 交付"}'
        )
    return calls


def tool_failure_diagnosis(
    recent: list[dict[str, Any]], *, threshold: int = 3,
) -> str | None:
    """连续 ≥threshold 次失败则返回诊断文案。"""
    if len(recent) < threshold:
        return None
    codes = [str(x.get("error_code") or "") for x in recent[-threshold:]]
    if not codes or any(not c for c in codes):
        # still diagnose if we have enough failures
        pass
    lines = [
        "【工具连续失败诊断】最近几次调用都失败了，原因是参数，不是写入配额或权限。",
    ]
    for x in recent[-threshold:]:
        lines.append(
            f"- {x.get('tool')} [{x.get('error_code')}] path={x.get('path') or '（无）'}"
        )
    top = codes[-1] if codes else "MISSING_FIELD"
    if top == "MISSING_FIELD":
        lines.append(
            '正确示例：write_file {"path": "README.md", "content": "# 项目"}'
        )
    elif top == "OUT_OF_SCOPE":
        lines.append("请用相对路径（以本任务工作区为根），如 README.md、src/main.py")
    elif top == "TRUNCATED":
        lines.append("请把内容拆短，或改用 append_file 分段写入")
    else:
        lines.append(
            '正确示例：write_file {"path": "README.md", "content": "# 项目"}'
        )
    return "\n".join(lines)
