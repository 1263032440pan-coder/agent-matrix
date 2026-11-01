"""V1.6d: 计划期冻结校验脚本——生成、静态门、差分冒烟、异步子进程执行。"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ERROR_CODE = "DELIVERY_VALIDATOR_INVALID"
SCRIPT_NAME = "_delivery_validate.py"
STDOUT_LIMIT = 64 * 1024
RUN_TIMEOUT_S = 30

_FORBIDDEN_MODULES = frozenset({
    "subprocess", "socket", "requests", "urllib", "httpx",
    "sqlite3", "importlib", "ctypes", "pickle", "marshal",
    "multiprocessing",
})
_FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "__import__",
    "system", "popen", "Popen",
})


class DeliveryValidatorError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.error_code = ERROR_CODE
        self.message = message


def validator_path(project_id: int) -> Path:
    return Path("data") / "tasks" / str(project_id) / SCRIPT_NAME


def clear_validator_files(project_id: int) -> None:
    p = validator_path(project_id)
    if p.exists():
        p.unlink()
    meta = p.with_suffix(".meta.json")
    if meta.exists():
        meta.unlink()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_validator_script_path(path: Path | str) -> bool:
    """路径是否指向冻结校验脚本（写保护用）。"""
    try:
        name = Path(path).name
    except (TypeError, ValueError):
        return False
    return name == SCRIPT_NAME


def static_check_source(source: str) -> None:
    """AST 静态门：语法 + 危导入/调用 + __main__ 入口。失败抛 DeliveryValidatorError。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise DeliveryValidatorError(
            f"校验脚本语法错误：{e}"
        ) from e

    has_main = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in _FORBIDDEN_MODULES:
                    raise DeliveryValidatorError(
                        f"校验脚本禁止导入：{a.name}"
                    )
        if isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in _FORBIDDEN_MODULES:
                raise DeliveryValidatorError(
                    f"校验脚本禁止导入：{node.module}"
                )
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in _FORBIDDEN_CALLS:
                raise DeliveryValidatorError(
                    f"校验脚本禁止调用：{name}"
                )
        # if __name__ == "__main__"
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "__name__"
                and any(
                    isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in node.comparators
                )
            ):
                has_main = True

    if not has_main:
        raise DeliveryValidatorError(
            "校验脚本须含 if __name__ == '__main__' 入口"
        )


def _strip_fence(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:python)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    return text


def _make_fixture_files(root: Path, manifest: dict, *, filled: bool) -> None:
    """按 match 造最小假文件（filled）或保持空（not filled）。"""
    root.mkdir(parents=True, exist_ok=True)
    if not filled:
        return
    for d in manifest.get("deliverables") or []:
        match = d.get("match") or {}
        exts = match.get("ext") or [".txt"]
        ext = exts[0] if exts else ".txt"
        if not ext.startswith("."):
            ext = f".{ext}"
        slot = match.get("slot")
        code_scan = bool(match.get("code_scan")) or slot == "source_code"
        rel_dir = match.get("rel_dir") or (
            f"deliverables/{slot}" if slot else ""
        )
        if code_scan:
            # 全树命中：落在 backend/，避免仅靠槽目录冒烟
            dest = root / "backend"
            dest.mkdir(parents=True, exist_ok=True)
            src_ext = ext if ext not in {".md", ".txt", ".rst", ".adoc"} else ".py"
            (dest / f"fixture{src_ext}").write_text("fixture", encoding="utf-8")
        elif slot and rel_dir:
            dest = root / Path(str(rel_dir).replace("\\", "/"))
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"fixture{ext}").write_text("fixture", encoding="utf-8")
        else:
            contains = match.get("name_contains") or []
            stem = contains[0] if contains else (d.get("id") or "item")
            safe = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", str(stem))[:40] or "item"
            (root / f"{safe}{ext}").write_text("fixture", encoding="utf-8")
        for pref in match.get("any_name_prefix") or []:
            (root / f"{pref}README.md").write_text("# ok", encoding="utf-8")


async def run_validator_script(
    script: Path,
    workspace: Path,
    ids: list[str],
    *,
    timeout: float = RUN_TIMEOUT_S,
) -> dict[str, Any]:
    """异步跑冻结脚本；返回 {ok, problems, raw, timed_out, error?}。"""
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
    }
    # 去掉空值
    env = {k: v for k, v in env.items() if v}
    ids_arg = ",".join(ids)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(script.resolve()),
        "--workspace",
        str(workspace.resolve()),
        "--ids",
        ids_arg,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(script.parent.resolve()),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "ok": False,
            "problems": [f"校验脚本超时（>{timeout}s）"],
            "raw": "",
            "timed_out": True,
        }
    raw = (stdout_b or b"").decode("utf-8", errors="replace")
    if len(raw) > STDOUT_LIMIT:
        return {
            "ok": False,
            "problems": ["校验脚本输出超过上限"],
            "raw": raw[:500],
            "timed_out": False,
        }
    text = raw.strip()
    # 剥可能的前后噪音：取第一个 { ... }
    brace = re.search(r"\{[\s\S]*\}", text)
    if not brace:
        err = (stderr_b or b"").decode("utf-8", errors="replace")[:200]
        return {
            "ok": False,
            "problems": [
                f"校验脚本未返回 JSON：{err or text[:120] or 'empty'}"
            ],
            "raw": text[:500],
            "timed_out": False,
        }
    try:
        data = json.loads(brace.group(0))
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "problems": [f"校验脚本 JSON 无法解析：{e}"],
            "raw": text[:500],
            "timed_out": False,
        }
    ok = bool(data.get("ok"))
    problems = data.get("problems") or []
    if not isinstance(problems, list):
        problems = [str(problems)]
    problems = [str(p) for p in problems]
    return {
        "ok": ok,
        "problems": problems,
        "raw": text[:500],
        "timed_out": False,
    }


async def differential_smoke(script: Path, manifest: dict) -> None:
    """满配必须 ok；空目录必须 fail 且 problems 足够；合并子集须单调。"""
    ids = [d["id"] for d in (manifest.get("deliverables") or [])]
    if not ids:
        raise DeliveryValidatorError("清单无 deliverables，无法冒烟")

    with tempfile.TemporaryDirectory(prefix="dv_full_") as td:
        full = Path(td)
        _make_fixture_files(full, manifest, filled=True)
        r = await run_validator_script(script, full, ids)
        if not r.get("ok"):
            raise DeliveryValidatorError(
                "差分冒烟失败：满足 match 的目录应 ok=true，"
                f"实际 problems={r.get('problems')}"
            )

    with tempfile.TemporaryDirectory(prefix="dv_empty_") as td:
        empty = Path(td)
        empty.mkdir(exist_ok=True)
        r = await run_validator_script(script, empty, ids)
        if r.get("ok"):
            raise DeliveryValidatorError(
                "差分冒烟失败：空目录不得 ok=true"
            )
        probs = r.get("problems") or []
        if len(probs) < len(ids):
            raise DeliveryValidatorError(
                f"差分冒烟失败：空目录 problems 应 ≥{len(ids)}，"
                f"实际 {len(probs)}：{probs}"
            )

    # SYS-114: 多子目录各自子集通过 → 合并根目录全量必须通过（单调性）
    if len(ids) >= 2:
        with tempfile.TemporaryDirectory(prefix="dv_mono_") as td:
            root = Path(td)
            mid = (len(ids) + 1) // 2
            chunks = [ids[:mid], ids[mid:]]
            for i, chunk in enumerate(chunks):
                if not chunk:
                    continue
                sub = root / f"part_{i}"
                sub.mkdir(parents=True, exist_ok=True)
                sub_man = {
                    **manifest,
                    "deliverables": [
                        d for d in (manifest.get("deliverables") or [])
                        if d["id"] in chunk
                    ],
                }
                _make_fixture_files(sub, sub_man, filled=True)
                r_sub = await run_validator_script(script, sub, chunk)
                if not r_sub.get("ok"):
                    raise DeliveryValidatorError(
                        "单调性冒烟失败：子集目录应 ok=true，"
                        f"ids={chunk} problems={r_sub.get('problems')}"
                    )
            r_merge = await run_validator_script(script, root, ids)
            if not r_merge.get("ok"):
                raise DeliveryValidatorError(
                    "单调性冒烟失败：子集各自通过合并后全量必须 ok=true，"
                    f"problems={r_merge.get('problems')}"
                )


def render_match_validator_source(manifest: dict) -> str:
    """确定性脚本（match 语义）——测试夹具 / LLM 示例同源实现。"""
    payload = json.dumps(manifest, ensure_ascii=False)
    return f'''# -*- coding: utf-8 -*-
"""Auto-generated delivery validator (match engine). Do not edit by agents."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

MANIFEST = json.loads({payload!r})


def _in_slot(path, rel_dir):
    p = str(path).replace("\\\\", "/").strip("/")
    r = str(rel_dir).replace("\\\\", "/").strip("/")
    if not p or not r:
        return False
    pl, rl = p.lower(), r.lower()
    if pl == rl or pl.startswith(rl + "/"):
        return True
    # 允许 1/ 或 part_0/ 等前缀嵌套（项目根 / 单调性合并）
    return ("/" + pl + "/").find("/" + rl + "/") >= 0


_DOC_ONLY = {{".md", ".txt", ".rst", ".adoc"}}
_DEFAULT_SRC = [".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".go", ".rs", ".toml", ".json"]


def check_match(paths, match):
    problems = []
    min_count = int(match.get("min_count") or 1)
    hits = list(paths)
    slot = match.get("slot")
    exts = list(match.get("ext") or [])
    code_scan = bool(match.get("code_scan")) or slot == "source_code"
    rel_dir = match.get("rel_dir") or (("deliverables/" + str(slot)) if slot else "")
    if code_scan and exts:
        code_exts = [e for e in exts if str(e).lower() not in _DOC_ONLY] or list(_DEFAULT_SRC)
        hits = [
            n for n in hits
            if any(Path(str(n)).name.lower().endswith(e) for e in code_exts)
        ]
    else:
        if slot:
            hits = [n for n in hits if _in_slot(n, rel_dir)]
        if exts:
            hits = [
                n for n in hits
                if any(Path(str(n)).name.lower().endswith(e) for e in exts)
            ]
        if (not slot) and "name_contains" in match:
            needles = match["name_contains"]
            hits = [n for n in hits if any(nd in Path(str(n)).name for nd in needles)]
    if len(hits) < min_count:
        bits = []
        if code_scan:
            bits.append("真实源码文件（全树，不含仅文档后缀）")
        elif slot:
            bits.append("slot=" + str(rel_dir) + "/")
        if match.get("ext"):
            bits.append("ext=" + str(match["ext"]))
        if (not slot) and (not code_scan) and match.get("name_contains"):
            bits.append("contains=" + str(match["name_contains"]))
        problems.append("缺少满足条件的文件（需>=" + str(min_count) + "）：" + "、".join(bits))
    if "any_name_prefix" in match:
        prefixes = [p.lower() for p in match["any_name_prefix"]]
        basenames = [Path(str(n)).name.lower() for n in paths]
        if not any(any(n.startswith(p) for p in prefixes) for n in basenames):
            problems.append("另需文件名前缀：" + str(match["any_name_prefix"]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    ws = Path(args.workspace)
    ids = [x for x in args.ids.split(",") if x.strip()]
    if not ids:
        print(json.dumps({{"ok": False, "problems": ["ids 为空"]}}, ensure_ascii=False))
        sys.exit(1)
    paths = []
    if ws.exists():
        for p in ws.rglob("*"):
            if p.is_file():
                try:
                    paths.append(p.relative_to(ws).as_posix())
                except ValueError:
                    paths.append(p.name)
    by_id = {{d["id"]: d for d in MANIFEST.get("deliverables") or []}}
    problems = []
    for did in ids:
        item = by_id.get(did)
        if item is None:
            problems.append("未知交付项 id=" + did)
            continue
        for p in check_match(paths, item.get("match") or {{}}):
            problems.append((item.get("name") or did) + ": " + p)
    ok = len(problems) == 0
    print(json.dumps({{"ok": ok, "problems": problems}}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
'''


async def extract_validator_source_from_llm(
    *,
    manifest: dict,
    materials_text: str = "",
    skill_hint: str = "",
) -> str:
    """LLM 生成脚本；失败抛 DeliveryValidatorError。"""
    from project_runtime.llm import (
        load_llm_settings,
        normalize_model_for_gateway,
    )
    from project_runtime.llm.provider import llm_complete

    cfg = load_llm_settings()
    if not (cfg.base_url and cfg.api_key and cfg.model):
        raise DeliveryValidatorError("无法生成校验脚本：LLM 未配置")
    model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)
    example = render_match_validator_source(manifest)
    prompt = (
        "请为下列交付清单编写一个独立的 Python 校验脚本。\n"
        "硬性要求：\n"
        "1. 只输出完整 .py 源码（可无 markdown 围栏），不要解释。\n"
        "2. CLI: python script.py --workspace PATH --ids id1,id2\n"
        "3. stdout 打印一行 JSON: {\"ok\": bool, \"problems\": [str,...]}；"
        "ok 时 exit 0，否则非 0。\n"
        "4. ids 为空时必须 ok=false 且 problems 说明错误。\n"
        "5. 只读 workspace 下文件；禁止网络、subprocess、sqlite、importlib、"
        "eval/exec。\n"
        "6. 必须含 if __name__ == '__main__':\n"
        "7. 对每个 id 按清单 match（ext/slot/rel_dir/name_contains/"
        "any_name_prefix/min_count/code_scan）检查；有 slot 且无 "
        "code_scan 时用相对路径落在 rel_dir（默认 deliverables/{{slot}}）"
        "下计数；code_scan 或 slot=source_code 时按源码后缀全树扫描"
        "（backend/src 等），不得仅用 .md 冒充源码，不得弱于 match。\n"
        "可参考下列示例结构（允许等价实现）：\n"
        f"```python\n{example[:3500]}\n```\n\n"
        f"【清单 JSON】\n{json.dumps(manifest, ensure_ascii=False)[:4000]}\n\n"
        f"【交付基线提示】\n{(skill_hint or '（无）')[:800]}\n\n"
        f"【材料摘要】\n{(materials_text or '（无）')[:2000]}"
    )
    result = await asyncio.wait_for(
        llm_complete(
            model=model_id,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8000,
            timeout=180,
        ),
        timeout=190,
    )
    if not result.get("ok"):
        raise DeliveryValidatorError(
            f"校验脚本生成失败：{result.get('error', 'LLM error')}"
        )
    from project_runtime.llm import llm_message_text

    text = llm_message_text(result["response"].choices[0].message)
    source = _strip_fence(text)
    if "def " not in source and "argparse" not in source:
        raise DeliveryValidatorError(
            "模型未返回可用 Python 脚本"
            + ("（空响应）" if not text.strip() else "")
        )
    return source


def write_validator_atomic(project_id: int, source: str) -> tuple[Path, str]:
    """原子写入脚本，返回 (path, sha256)。"""
    path = validator_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".py.tmp")
    tmp.write_text(source, encoding="utf-8")
    tmp.replace(path)
    return path, sha256_file(path)


async def generate_and_install_validator(
    project_id: int,
    manifest: dict,
    *,
    materials_text: str = "",
    skill_hint: str = "",
    source_override: str | None = None,
) -> str:
    """清旧 → 生成/覆盖源码 → 静态门 → 差分冒烟 → 落盘。返回 hash。

    source_override: 测试注入合法脚本，跳过 LLM。
    无 override 时：先试 LLM 脚本；冒烟失败则回退确定性 match 引擎脚本。
    """
    clear_validator_files(project_id)
    try:
        candidates: list[tuple[str, str]] = []
        if source_override is not None:
            candidates.append(("override", source_override))
        else:
            try:
                llm_src = await extract_validator_source_from_llm(
                    manifest=manifest,
                    materials_text=materials_text,
                    skill_hint=skill_hint,
                )
                candidates.append(("llm", llm_src))
            except DeliveryValidatorError as e:
                logger.warning(
                    "validator LLM extract failed, will try deterministic: %s",
                    e.message,
                )
            candidates.append(
                ("deterministic", render_match_validator_source(manifest)),
            )

        last_err: DeliveryValidatorError | None = None
        for label, source in candidates:
            try:
                static_check_source(source)
                path, digest = write_validator_atomic(project_id, source)
                await differential_smoke(path, manifest)
                if label == "deterministic" and source_override is None:
                    logger.warning(
                        "project %s: installed deterministic validator "
                        "after LLM/override smoke path failed",
                        project_id,
                    )
                return digest
            except DeliveryValidatorError as e:
                last_err = e
                clear_validator_files(project_id)
                logger.warning(
                    "validator candidate %s failed smoke/static: %s",
                    label, e.message,
                )
                continue
        raise last_err or DeliveryValidatorError("校验脚本安装失败：无可用候选")
    except DeliveryValidatorError:
        clear_validator_files(project_id)
        raise
    except Exception as e:  # noqa: BLE001
        clear_validator_files(project_id)
        raise DeliveryValidatorError(
            f"校验脚本安装失败：{e}"
        ) from e


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
