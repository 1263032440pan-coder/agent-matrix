"""令牌 Stop：仅扫本任务目录（SYS-123）；槽令牌认相对目录（SYS-136）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def _rel_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _slot_rel_dir(
    tid: str,
    token: dict[str, Any],
    deliverables_by_id: dict[str, dict],
) -> str:
    """从 slot: 令牌或清单项还原相对槽目录。"""
    label = str(token.get("label") or "").strip().replace("\\", "/").strip("/")
    if label:
        return label.rstrip("/")
    parts = tid.split(":", 2)
    slot = parts[2] if len(parts) >= 3 else ""
    did = parts[1] if len(parts) >= 2 else ""
    item = deliverables_by_id.get(did) or {}
    match = item.get("match") or {}
    rel = str(match.get("rel_dir") or "").strip().replace("\\", "/")
    if rel:
        return rel.strip("/")
    slot = str(match.get("slot") or slot).strip()
    return f"deliverables/{slot}" if slot else ""


def slot_dirs_for_tokens(
    tokens: list[dict[str, Any]] | None,
    deliverables_by_id: dict[str, dict] | None = None,
) -> list[str]:
    """从 slot: / count: 令牌推出必写槽相对目录（带尾 /）。"""
    deliverables_by_id = deliverables_by_id or {}
    out: list[str] = []
    seen: set[str] = set()

    def _add(rel: str) -> None:
        r = str(rel or "").replace("\\", "/").strip().strip("/")
        if not r:
            return
        key = r.lower() + "/"
        if key in seen:
            return
        seen.add(key)
        out.append(r + "/")

    for t in tokens or []:
        tid = str(t.get("id") or "")
        if tid.startswith("slot:"):
            rel = _slot_rel_dir(tid, t, deliverables_by_id)
            if rel:
                _add(rel)
            continue
        if not tid.startswith("count:"):
            continue
        did = str(t.get("deliverable_id") or "")
        if not did:
            parts = tid.split(":")
            if len(parts) >= 2:
                did = parts[1]
        item = deliverables_by_id.get(did) or {}
        match = item.get("match") or {}
        if not match.get("slot"):
            continue
        rd = str(
            match.get("rel_dir") or f"deliverables/{match['slot']}"
        ).replace("\\", "/").strip("/")
        _add(rd)
    return out


@dataclass
class ObligationStopResult:
    """令牌 Stop 结构化结果（SYS-139）。"""

    problems: list[str] = field(default_factory=list)
    unmet: list[dict[str, Any]] = field(default_factory=list)
    scan_root: str = "task"
    scan_path: str = ""

    def ok(self) -> bool:
        return not self.problems


def check_obligation_tokens(
    task_dir: Path | str,
    tokens: list[dict[str, Any]] | None,
    *,
    deliverables_by_id: dict[str, dict] | None = None,
) -> list[str]:
    """本任务目录内合取检查令牌；返回 problems（空=通过）。"""
    return check_obligation_tokens_detailed(
        task_dir,
        tokens,
        deliverables_by_id=deliverables_by_id,
    ).problems


def check_obligation_tokens_detailed(
    task_dir: Path | str,
    tokens: list[dict[str, Any]] | None,
    *,
    deliverables_by_id: dict[str, dict] | None = None,
    scan_path: str | None = None,
) -> ObligationStopResult:
    """本任务目录内合取检查；返回 problems + unmet（禁止事后反解析）。"""
    root = Path(task_dir)
    files = _iter_files(root)
    names = [p.name for p in files]
    names_lower = [n.lower() for n in names]
    rel_paths = [_rel_posix(p, root) for p in files]
    result = ObligationStopResult(
        scan_root="task",
        scan_path=scan_path or str(root).replace("\\", "/"),
    )
    toks = list(tokens or [])
    if not toks:
        return result

    deliverables_by_id = deliverables_by_id or {}

    def _fail(
        problem: str,
        *,
        token_id: str | None = None,
        kind: str = "other",
        hint: str | None = None,
    ) -> None:
        result.problems.append(problem)
        result.unmet.append({
            "token_id": token_id,
            "kind": kind,
            "hint": hint or problem,
        })

    exts_by_did: dict[str, list[tuple[str, str]]] = {}
    # did -> list[(ext, token_id)]
    prefixes: list[tuple[str, str]] = []  # (pref, token_id)
    names_need: list[tuple[str, str]] = []
    slots: list[tuple[str, str]] = []  # (rel_dir, tid)
    count_quota = 0
    count_dids: list[str] = []
    count_token_ids: list[str] = []
    outputs: list[tuple[str, str]] = []
    features: list[tuple[str, str]] = []

    for t in toks:
        tid = str(t.get("id") or "")
        if tid.startswith("ext:"):
            parts = tid.split(":", 2)
            if len(parts) >= 3:
                did = str(t.get("deliverable_id") or parts[1] or "_")
                e = parts[2].lower()
                if e == "any":
                    for raw in t.get("exts") or []:
                        ee = str(raw).strip().lower()
                        if not ee:
                            continue
                        if not ee.startswith("."):
                            ee = f".{ee}"
                        exts_by_did.setdefault(did, []).append((ee, tid))
                else:
                    if not e.startswith("."):
                        e = f".{e}"
                    exts_by_did.setdefault(did, []).append((e, tid))
        elif tid.startswith("prefix:"):
            parts = tid.split(":", 2)
            val = (t.get("label") or (parts[2] if len(parts) >= 3 else "")).lower()
            if val:
                prefixes.append((val.rstrip("*"), tid))
        elif tid.startswith("slot:"):
            rel = _slot_rel_dir(tid, t, deliverables_by_id)
            if rel:
                slots.append((rel, tid))
        elif tid.startswith("name:"):
            parts = tid.split(":", 2)
            val = t.get("label") or (parts[2] if len(parts) >= 3 else "")
            if val:
                names_need.append((str(val).lower(), tid))
        elif tid.startswith("count:"):
            count_quota += int(t.get("quota") or 1)
            parts = tid.split(":")
            if len(parts) >= 2:
                count_dids.append(parts[1])
            count_token_ids.append(tid)
        elif tid.startswith("output:"):
            label = str(t.get("label") or "").strip()
            if label:
                outputs.append((label.lower(), tid))
        elif tid.startswith("feature:"):
            label = str(t.get("label") or "").strip()
            if label:
                features.append((label.lower(), tid))

    exts: list[str] = []
    for pairs in exts_by_did.values():
        exts.extend(e for e, _ in pairs)

    for did, pairs in exts_by_did.items():
        uniq = list(dict.fromkeys(e for e, _ in pairs))
        if not uniq:
            continue
        if not any(
            any(n.lower().endswith(e) for e in uniq) for n in names
        ):
            tid0 = pairs[0][1] if pairs else f"ext:{did}:any"
            _fail(
                "令牌缺扩展名文件（需其一）：" + str(uniq),
                token_id=tid0,
                kind="ext",
            )

    for pref, tid in prefixes:
        if not any(n.startswith(pref) for n in names_lower):
            _fail(
                f"令牌缺前缀文件：{pref}*",
                token_id=tid,
                kind="prefix",
                hint=f"本任务目录需要 {pref}* 文件",
            )

    for rel_dir, tid in slots:
        rl = rel_dir.replace("\\", "/").strip("/").lower()
        under = []
        for p, rp in zip(files, rel_paths):
            pl = rp.replace("\\", "/").strip("/").lower()
            if (
                pl == rl
                or pl.startswith(rl + "/")
                or ("/" + pl + "/").find("/" + rl + "/") >= 0
            ):
                under.append(p)
        if not under:
            _fail(
                f"令牌缺槽目录文件：{rel_dir}/",
                token_id=tid,
                kind="slot",
                hint=f"槽目录需有文件：{rel_dir}/",
            )

    for needle, tid in names_need:
        if not any(needle in n for n in names_lower):
            _fail(
                f"令牌缺名称包含：{needle}",
                token_id=tid,
                kind="name",
            )

    if count_quota > 0:
        cand = files
        filter_exts = list(exts)
        slot_dirs: list[str] = []
        if count_dids:
            for did in count_dids:
                item = deliverables_by_id.get(did) or {}
                match = item.get("match") or {}
                if match.get("slot"):
                    rd = str(
                        match.get("rel_dir")
                        or f"deliverables/{match['slot']}"
                    ).replace("\\", "/").strip("/")
                    if rd:
                        slot_dirs.append(rd)
                if not filter_exts:
                    for e in match.get("ext") or []:
                        ee = str(e).strip().lower()
                        if not ee:
                            continue
                        if not ee.startswith("."):
                            ee = f".{ee}"
                        filter_exts.append(ee)
        if slot_dirs:
            cand = []
            for p, rp in zip(files, rel_paths):
                pl = rp.replace("\\", "/").strip("/").lower()
                if any(
                    pl == sd.replace("\\", "/").strip("/").lower()
                    or pl.startswith(
                        sd.replace("\\", "/").strip("/").lower() + "/"
                    )
                    or ("/" + pl + "/").find(
                        "/" + sd.replace("\\", "/").strip("/").lower() + "/"
                    ) >= 0
                    for sd in slot_dirs
                ):
                    cand.append(p)
        if filter_exts:
            cand = [
                p for p in cand
                if any(p.name.lower().endswith(e) for e in filter_exts)
            ]
        if len(cand) < count_quota:
            tid0 = count_token_ids[0] if count_token_ids else None
            _fail(
                f"令牌数量不足：需要 ≥{count_quota}，本目录 {len(cand)}",
                token_id=tid0,
                kind="count",
            )

    for label, tid in outputs:
        if not any(label in n for n in names_lower):
            _fail(
                f"令牌缺 stage_output 文件名：{label}",
                token_id=tid,
                kind="output",
            )

    for label, tid in features:
        if not any(label in n for n in names_lower):
            _fail(
                f"令牌缺功能点文件名：{label}",
                token_id=tid,
                kind="feature",
            )

    return result
