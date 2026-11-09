"""义务（后缀/令牌/清单）→ 所需插件 + 缺口计算。

主门禁依据义务，不依赖 task_type 字符串归一（防 misc 漏检）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 受控后缀 → 插件裸名（与 office.controlled 对齐，插件名为可挂白名单 ID）
_EXT_TO_PLUGIN: dict[str, str] = {
    ".pptx": "office_pptx",
    ".docx": "office_docx",
    ".xlsx": "office_xlsx",
    ".pdf": "office_pdf",
    ".png": "image_raster",
    ".jpg": "image_raster",
    ".jpeg": "image_raster",
    ".webp": "image_raster",
}

_CODEISH_OUTPUT = re.compile(
    r"源码|代码|实现|README|可运行|工程|模块|cli|API",
    re.IGNORECASE,
)


def _norm_ext(raw: object) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if not s.startswith("."):
        s = "." + s
    return s


def _plugins_from_exts(exts: list[object] | None) -> set[str]:
    out: set[str] = set()
    for e in exts or []:
        ext = _norm_ext(e)
        if ext and ext in _EXT_TO_PLUGIN:
            out.add(_EXT_TO_PLUGIN[ext])
    return out


def _with_pdf_implies_docx(need: set[str]) -> set[str]:
    """PDF 铁律：.pdf 义务隐含 office_docx（须先 Word 再转换）。"""
    if "office_pdf" in need:
        need = set(need)
        need.add("office_docx")
    return need


def required_plugins_for_tokens(tokens: list[dict] | None) -> set[str]:
    need: set[str] = set()
    for tok in tokens or []:
        if not isinstance(tok, dict):
            continue
        need |= _plugins_from_exts(tok.get("exts") or [])
        # label 如 ".pptx"
        lab = _norm_ext(tok.get("label"))
        if lab and lab in _EXT_TO_PLUGIN:
            need.add(_EXT_TO_PLUGIN[lab])
    return _with_pdf_implies_docx(need)


def required_plugins_for_manifest_ids(
    deliverable_ids: list[str] | None,
    manifest: dict | None,
) -> set[str]:
    if not deliverable_ids or not manifest:
        return set()
    by_id = {
        str(d.get("id")): d
        for d in (manifest.get("deliverables") or [])
        if isinstance(d, dict) and d.get("id")
    }
    need: set[str] = set()
    for did in deliverable_ids:
        d = by_id.get(str(did))
        if not d:
            continue
        match = d.get("match") if isinstance(d.get("match"), dict) else {}
        need |= _plugins_from_exts(match.get("ext") or [])
    return need


def required_plugins_for_playbook_ref(
    playbook_ref: str | dict | None,
    *,
    manifest: dict | None = None,
) -> set[str]:
    if playbook_ref is None or playbook_ref == "":
        return set()
    if isinstance(playbook_ref, str):
        try:
            ref = json.loads(playbook_ref)
        except (ValueError, TypeError):
            return set()
    else:
        ref = playbook_ref
    if not isinstance(ref, dict):
        return set()

    need = required_plugins_for_tokens(ref.get("obligation_tokens") or [])
    ids = ref.get("deliverable_ids") or []
    if ids and manifest:
        need |= required_plugins_for_manifest_ids(list(ids), manifest)
    return _with_pdf_implies_docx(need)


def plugin_gap(
    need: set[str],
    have_plugins: list[str] | None,
    *,
    available_plugins: set[str] | frozenset[str] | None = None,
) -> set[str]:
    """所需 − 有效能力。

    have None = 白名单不限；若同时给 available_plugins，则缺口 = need − available。
    available_plugins：已注册且 enabled 且 usable 的裸名集合（方案 O1）。
    """
    def _bares(items: set[str] | frozenset[str] | list[str]) -> set[str]:
        out: set[str] = set()
        for h in items:
            out.add(str(h))
            out.add(str(h).split(":", 1)[-1])
        return out

    if have_plugins is None:
        if available_plugins is None:
            return set()
        avail = _bares(set(available_plugins))
        return {p for p in need if p not in avail and f"tool:{p}" not in avail}

    from project_runtime.worker.tools import expand_tool_whitelist

    expanded = expand_tool_whitelist(list(have_plugins)) or []
    have = _bares(set(expanded))
    if available_plugins is not None:
        have &= _bares(set(available_plugins))
    return {p for p in need if p not in have and f"tool:{p}" not in have}


def available_tool_plugins(registry=None) -> set[str]:
    """当前 registry 上 usable 的 TOOL 裸名（含 llm_name）。"""
    from project_runtime.extension.registry import CapabilityType
    from project_runtime.extension.shared import shared_registry
    from project_runtime.worker.tool_surface import provider_is_usable

    reg = registry or shared_registry()
    out: set[str] = set()
    for info in reg.list_providers(CapabilityType.TOOL):
        if not info.enabled or info.provider is None:
            continue
        if not provider_is_usable(info.provider, reg):
            continue
        out.add(info.name.split(":", 1)[-1])
        llm_n = getattr(info.provider, "llm_name", None)
        if llm_n:
            out.add(str(llm_n).split(":", 1)[-1])
    return out


def office_suffixes_of_need(need: set[str]) -> set[str]:
    rev = {v: k for k, v in _EXT_TO_PLUGIN.items() if v.startswith("office_")}
    # image_raster maps multiple; skip for office-only
    return {rev[p] for p in need if p in rev}


# 受控媒体插件（办公二进制 + 位图）——与 write_file 拒写面一致
_CONTROLLED_MEDIA_PLUGINS = frozenset({
    "office_pptx", "office_docx", "office_xlsx", "office_pdf",
    "image_raster",
})
_MEDIA_DELIVERY_TAGS = frozenset({
    "ppt", "pptx", "doc", "docx", "xlsx", "excel", "pdf",
    "image", "img", "office", "slides",
})


def is_code_intent(ref: dict) -> bool:
    tags = [str(t).lower() for t in (ref.get("delivery_tags") or [])]
    if "code" in tags and not (set(tags) & _MEDIA_DELIVERY_TAGS):
        return True
    outs = " ".join(str(x) for x in (ref.get("stage_outputs") or []))
    if _CODEISH_OUTPUT.search(outs) and not (set(tags) & _MEDIA_DELIVERY_TAGS):
        return True
    return False


def is_office_heavy_obligation(need: set[str]) -> bool:
    """义务 ⊆ 受控媒体插件（办公/位图），含纯图片。"""
    if not need:
        return False
    return need <= _CONTROLLED_MEDIA_PLUGINS


def detect_code_media_mismatch(
    playbook_ref: str | dict | None,
    *,
    manifest: dict | None = None,
) -> str | None:
    """代码意图 ↔ 受控媒体义务矛盾。

    返回 CODE_MEDIA_MISMATCH；若义务仅 pptx 另标 CODE_PPT_MISMATCH（兼容旧称）。
    """
    if playbook_ref is None or playbook_ref == "":
        return None
    if isinstance(playbook_ref, str):
        try:
            ref = json.loads(playbook_ref)
        except (ValueError, TypeError):
            return None
    else:
        ref = dict(playbook_ref)
    if not isinstance(ref, dict):
        return None
    need = required_plugins_for_playbook_ref(ref, manifest=manifest)
    if not (is_code_intent(ref) and is_office_heavy_obligation(need)):
        return None
    if need == {"office_pptx"} or need <= {"office_pptx", "office_inspect"}:
        return "CODE_PPT_MISMATCH"
    return "CODE_MEDIA_MISMATCH"


def detect_code_ppt_mismatch(
    playbook_ref: str | dict | None,
    *,
    manifest: dict | None = None,
) -> str | None:
    """兼容旧名：任意 code↔媒体矛盾均非 None（含 CODE_MEDIA_MISMATCH）。"""
    return detect_code_media_mismatch(playbook_ref, manifest=manifest)


_PLUGIN_CONTRACT_HINTS: dict[str, str] = {
    "office_pptx": "PPT 幻灯片 演示文稿",
    "office_docx": "Word docx 报告",
    "office_xlsx": "Excel xlsx 表格",
    "office_pdf": "PDF 报告",
    "image_raster": "位图 示意图 海报",
    "arch_diagram": "架构图 mermaid",
}


def contract_hint_for_plugins(need: set[str], base: str | None = None) -> str:
    """为派生/契约增量拼关键词（让 SAFE_INCREMENT 扫到）。"""
    bits = [base or ""]
    for p in sorted(need):
        bits.append(_PLUGIN_CONTRACT_HINTS.get(p, p))
    return " ".join(x for x in bits if x).strip()


def split_office_tokens(
    tokens: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    """拆分义务令牌：受控媒体（办公/位图）vs 其余。"""
    office: list[dict] = []
    rest: list[dict] = []
    for tok in tokens or []:
        if not isinstance(tok, dict):
            rest.append(tok)  # type: ignore[arg-type]
            continue
        need = required_plugins_for_tokens([tok])
        if need & _CONTROLLED_MEDIA_PLUGINS:
            office.append(tok)
        else:
            rest.append(tok)
    return office, rest


def _media_tags_and_label(need: set[str]) -> tuple[list[str], str, str]:
    """子节点 delivery_tags / 短名 / 产出描述。"""
    tags: list[str] = []
    if "office_pptx" in need:
        tags.append("ppt")
    if "office_docx" in need:
        tags.append("doc")
    if "office_xlsx" in need:
        tags.append("xlsx")
    if "office_pdf" in need:
        tags.append("pdf")
    if "image_raster" in need:
        tags.append("image")
    if not tags:
        tags = ["office"]
    if need == {"office_pptx"}:
        short, outs = "PPT", "演示文稿 PPT"
    elif need == {"image_raster"}:
        short, outs = "图", "位图/示意图"
    elif need <= {"office_docx", "office_pdf"} and need:
        short, outs = "文档", "办公文档"
    elif need == {"office_xlsx"}:
        short, outs = "表", "表格台账"
    else:
        short, outs = "办公", "办公/媒体交付物"
    return tags, short, outs


def resolve_code_media_in_node(
    nd: dict,
    *,
    manifest: dict | None = None,
    new_key: str,
) -> dict | None:
    """消解 code↔受控媒体矛盾：返回新媒体节点，或 None（含仅改标签）。"""
    pref = dict(nd.get("playbook_ref") or {})
    merged = {
        **pref,
        "obligation_tokens": list(
            nd.get("obligation_tokens") or pref.get("obligation_tokens") or []
        ),
        "deliverable_ids": list(
            nd.get("deliverable_ids") or pref.get("deliverable_ids") or []
        ),
        "delivery_tags": list(pref.get("delivery_tags") or []),
        "stage_outputs": list(
            nd.get("stage_outputs") or pref.get("stage_outputs") or []
        ),
    }
    kind = detect_code_media_mismatch(merged, manifest=manifest)
    if kind is None:
        return None

    toks = list(merged["obligation_tokens"])
    office_toks, code_toks = split_office_tokens(toks)
    by_id = {
        str(d.get("id")): d
        for d in ((manifest or {}).get("deliverables") or [])
        if isinstance(d, dict) and d.get("id")
    }

    if not office_toks:
        office_ids: list[str] = []
        code_ids: list[str] = []
        for did in merged["deliverable_ids"]:
            d = by_id.get(str(did)) or {}
            match = d.get("match") if isinstance(d.get("match"), dict) else {}
            need = _plugins_from_exts(match.get("ext") or [])
            if need & _CONTROLLED_MEDIA_PLUGINS:
                office_ids.append(str(did))
            else:
                code_ids.append(str(did))
        if not office_ids:
            tags = [
                t for t in merged["delivery_tags"]
                if t.lower() != "code"
            ]
            for t in ("office", "ppt", "image"):
                if t not in tags:
                    tags.append(t)
                    break
            pref["delivery_tags"] = tags
            pref["code_media_resolved"] = "retag"
            nd["playbook_ref"] = pref
            return None
        office_toks = []
        code_toks = toks
        merged_office_ids, merged_code_ids = office_ids, code_ids
        child_need = set()
        for did in office_ids:
            d = by_id.get(str(did)) or {}
            match = d.get("match") if isinstance(d.get("match"), dict) else {}
            child_need |= _plugins_from_exts(match.get("ext") or [])
    else:
        office_exts: set[str] = set()
        for t in office_toks:
            for e in t.get("exts") or []:
                ne = _norm_ext(e)
                if ne:
                    office_exts.add(ne)
        merged_office_ids = []
        merged_code_ids = []
        for did in merged["deliverable_ids"]:
            d = by_id.get(str(did)) or {}
            match = d.get("match") if isinstance(d.get("match"), dict) else {}
            exts = {_norm_ext(x) for x in (match.get("ext") or [])}
            if exts & office_exts:
                merged_office_ids.append(str(did))
            else:
                merged_code_ids.append(str(did))
        child_need = required_plugins_for_tokens(office_toks)

    media_tags, _short, outs = _media_tags_and_label(child_need)

    nd["obligation_tokens"] = code_toks
    nd["deliverable_ids"] = merged_code_ids
    pref["obligation_tokens"] = code_toks
    pref["deliverable_ids"] = merged_code_ids
    tags = [
        t for t in list(pref.get("delivery_tags") or [])
        if str(t).lower() not in _MEDIA_DELIVERY_TAGS
    ]
    if "code" not in tags:
        tags.append("code")
    pref["delivery_tags"] = tags
    pref["code_media_resolved"] = "split"
    pref["code_ppt_resolved"] = "split"  # 兼容旧字段
    nd["playbook_ref"] = pref

    child_pref = {
        "task_name": new_key,
        "deliverable_ids": merged_office_ids,
        "obligation_tokens": office_toks,
        "delivery_tags": media_tags,
        "stage_outputs": [outs],
        "code_stage": False,
        "assignment_role": "交付补全",
        "decomposed_from": nd.get("task_key"),
        "code_media_resolved": "split_child",
        "code_ppt_resolved": "split_child",
        "mismatch_kind": kind,
    }
    return {
        "task_key": new_key,
        "role": "交付补全",
        "task_type": "交付打包",
        "code_stage": False,
        "deliverable_ids": merged_office_ids,
        "obligation_tokens": office_toks,
        "stage_outputs": [outs],
        "contract": f"产出{outs}",
        "status": "CREATED",
        "frozen": False,
        "bin_packed": False,
        "decomposed_from": nd.get("task_key"),
        "playbook_ref": child_pref,
    }


def resolve_code_ppt_in_node(
    nd: dict,
    *,
    manifest: dict | None = None,
    new_key: str,
) -> dict | None:
    """兼容旧名。"""
    return resolve_code_media_in_node(
        nd, manifest=manifest, new_key=new_key,
    )


def resolve_code_media_mismatches(
    graph: dict,
    *,
    manifest: dict | None = None,
) -> dict[str, Any]:
    """规划期消解 code↔办公/位图矛盾（只改内存图；落库与整闸由认领链后续步骤负责）。"""
    nodes = graph.setdefault("nodes", {})
    deps = graph.setdefault("deps", [])
    split = 0
    retagged = 0
    for key in list(nodes.keys()):
        nd = nodes[key]
        if nd.get("frozen"):
            continue
        # 先看义务再定前缀
        pref = nd.get("playbook_ref") if isinstance(
            nd.get("playbook_ref"), dict
        ) else {}
        probe = {
            **(pref or {}),
            "obligation_tokens": list(
                nd.get("obligation_tokens") or (pref or {}).get(
                    "obligation_tokens"
                ) or []
            ),
            "deliverable_ids": list(
                nd.get("deliverable_ids")
                or (pref or {}).get("deliverable_ids")
                or []
            ),
        }
        need = required_plugins_for_playbook_ref(probe, manifest=manifest)
        _, short, _ = _media_tags_and_label(need & _CONTROLLED_MEDIA_PLUGINS)
        child_key = f"交付补全-{short}-{key}"
        n = 1
        while child_key in nodes:
            n += 1
            child_key = f"交付补全-{short}-{key}-{n}"
        before_tags = list((pref or {}).get("delivery_tags") or [])
        child = resolve_code_media_in_node(
            nd, manifest=manifest, new_key=child_key,
        )
        after_tags = list(
            (nd.get("playbook_ref") or {}).get("delivery_tags") or []
        )
        if child is None:
            if before_tags != after_tags:
                retagged += 1
            continue
        nodes[child_key] = child
        deps.append((child_key, key))
        split += 1
    return {
        "code_media_split": split,
        "code_media_retagged": retagged,
        # 兼容旧统计键
        "code_ppt_split": split,
        "code_ppt_retagged": retagged,
    }


def resolve_code_ppt_mismatches(
    graph: dict,
    *,
    manifest: dict | None = None,
) -> dict[str, Any]:
    """兼容旧名。"""
    return resolve_code_media_mismatches(graph, manifest=manifest)


def binary_denied_hint(
    path: str,
    *,
    visible_tools: list[str] | None = None,
) -> dict[str, Any]:
    """增强 BINARY_SUFFIX_DENIED 回执。"""
    from project_runtime.office.controlled import (
        CONTROLLED_BINARY_SUFFIXES,
        controlled_suffix_of,
    )
    from project_runtime.worker.tools import tool_error

    suf = controlled_suffix_of(path) or ""
    tool = CONTROLLED_BINARY_SUFFIXES.get(suf, "专用渲染工具")
    primary = str(tool).split()[0].split("或")[0].strip()
    vis = visible_tools
    if vis is None:
        vis_txt = "（未知）"
    elif not vis:
        vis_txt = "无"
    else:
        short = [v.removeprefix("tool:") for v in vis]
        vis_txt = ", ".join(short[:24])
        if primary not in short and f"tool:{primary}" not in vis:
            vis_txt += f"；当前无 {primary}"
    return tool_error(
        "BINARY_SUFFIX_DENIED",
        f"禁止用 write_file/append_file 写入 {suf or '二进制'}——"
        "请只写 UTF-8 规格（.json/.md），再调用专用工具生成",
        f"请改用 {tool}，path 使用清单相对路径（如 deliverables/.../文件{suf}）。"
        f"本 Agent 当前可见工具：{vis_txt}",
    )
