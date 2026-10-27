"""V1.6c: 动态交付清单——结构化 JSON + 确定性 match + 计划期抽取。

路径 1: LLM 输出 deliverables[]; schema 校验失败 → DELIVERY_MANIFEST_INVALID。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ERROR_CODE = "DELIVERY_MANIFEST_INVALID"
_ALLOWED_MATCH_KEYS = frozenset({
    "ext", "name_contains", "any_name_prefix", "min_count",
})


class DeliveryManifestError(ValueError):
    """清单 JSON/schema 非法——规划应中止并 toast。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.error_code = ERROR_CODE
        self.message = message


def validate_manifest(data: Any) -> dict:
    """校验并归一化 manifest；失败抛 DeliveryManifestError。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise DeliveryManifestError(
                f"交付清单 JSON 无法解析：{e}"
            ) from e
    if not isinstance(data, dict):
        raise DeliveryManifestError("交付清单须为 JSON 对象")
    raw_list = data.get("deliverables")
    if not isinstance(raw_list, list) or not raw_list:
        raise DeliveryManifestError(
            "交付清单须含非空 deliverables 数组"
        )
    seen_ids: set[str] = set()
    normalized: list[dict] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise DeliveryManifestError(
                f"deliverables[{i}] 须为对象"
            )
        did = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not did or not name:
            raise DeliveryManifestError(
                f"deliverables[{i}] 须含非空 id 与 name"
            )
        if did in seen_ids:
            raise DeliveryManifestError(f"重复的 deliverable id: {did}")
        seen_ids.add(did)
        match = item.get("match")
        if not isinstance(match, dict) or not match:
            raise DeliveryManifestError(
                f"deliverables[{i}] ({did}) 须含非空 match 对象"
            )
        unknown = set(match.keys()) - _ALLOWED_MATCH_KEYS
        if unknown:
            raise DeliveryManifestError(
                f"deliverables[{i}].match 含未知字段 "
                f"{sorted(unknown)}（仅允许 {sorted(_ALLOWED_MATCH_KEYS)}）"
            )
        norm_match: dict[str, Any] = {}
        if "ext" in match:
            ext = match["ext"]
            if not isinstance(ext, list) or not all(
                isinstance(x, str) for x in ext
            ):
                raise DeliveryManifestError(
                    f"deliverables[{i}].match.ext 须为字符串数组"
                )
            norm_match["ext"] = [
                x.lower() if x.startswith(".") else f".{x.lower()}"
                for x in ext
            ]
        for key in ("name_contains", "any_name_prefix"):
            if key in match:
                val = match[key]
                if not isinstance(val, list) or not all(
                    isinstance(x, str) for x in val
                ):
                    raise DeliveryManifestError(
                        f"deliverables[{i}].match.{key} 须为字符串数组"
                    )
                norm_match[key] = val
        min_count = int(match.get("min_count") or 1)
        if min_count < 1:
            raise DeliveryManifestError(
                f"deliverables[{i}].match.min_count 须 ≥ 1"
            )
        norm_match["min_count"] = min_count
        if not any(k in norm_match for k in (
            "ext", "name_contains",
        )):
            raise DeliveryManifestError(
                f"deliverables[{i}].match 至少须有 ext 或 name_contains"
            )
        normalized.append({
            "id": did, "name": name, "match": norm_match,
        })
    return {
        "version": int(data.get("version") or 1),
        "source": str(data.get("source") or "plan_extract"),
        "deliverables": normalized,
    }


def check_match(file_names: list[str], match: dict) -> list[str]:
    """对一组文件名做 match 检查；返回问题文案列表（空=通过）。"""
    names = list(file_names)
    lower = [n.lower() for n in names]
    problems: list[str] = []
    min_count = int(match.get("min_count") or 1)

    def _primary_hits() -> list[str]:
        hits = names
        if "ext" in match:
            exts = match["ext"]
            hits = [
                n for n in hits
                if any(n.lower().endswith(e) for e in exts)
            ]
        if "name_contains" in match:
            needles = match["name_contains"]
            hits = [
                n for n in hits
                if any(nd in n for nd in needles)
            ]
        return hits

    primary = _primary_hits()
    if len(primary) < min_count:
        bits = []
        if match.get("ext"):
            bits.append(f"扩展名 {match['ext']}")
        if match.get("name_contains"):
            bits.append(f"文件名含 {match['name_contains']}")
        problems.append(
            f"缺少满足条件的文件（需 ≥{min_count}）："
            + "、".join(bits)
        )
    if "any_name_prefix" in match:
        prefixes = [p.lower() for p in match["any_name_prefix"]]
        if not any(
            any(n.startswith(p) for p in prefixes) for n in lower
        ):
            problems.append(
                f"另需文件名以前缀之一开头：{match['any_name_prefix']}"
            )
    return problems


def check_deliverables(
    file_names: list[str],
    manifest: dict,
    deliverable_ids: list[str],
) -> list[str]:
    """按 ids 检查；返回「名称: 问题」列表。"""
    by_id = {d["id"]: d for d in manifest.get("deliverables") or []}
    problems: list[str] = []
    for did in deliverable_ids:
        item = by_id.get(did)
        if item is None:
            problems.append(f"未知交付项 id={did}")
            continue
        for p in check_match(file_names, item["match"]):
            problems.append(f"{item['name']}: {p}")
    return problems


def workspace_file_names(ws_root: Path) -> list[str]:
    if not ws_root.exists():
        return []
    return [p.name for p in ws_root.rglob("*") if p.is_file()]


def map_legacy_tags_to_ids(manifest: dict, tags: list[str]) -> list[str]:
    """迁移期: ppt/code/test_report → 清单 id（按 match 启发式）。"""
    ids: list[str] = []
    for tag in tags:
        for d in manifest.get("deliverables") or []:
            m = d.get("match") or {}
            ext = [e.lower() for e in (m.get("ext") or [])]
            contains = m.get("name_contains") or []
            if tag == "ppt" and any(e.endswith("pptx") for e in ext):
                ids.append(d["id"])
            elif tag == "code" and any(
                e in (".py", ".js", ".ts", ".java") for e in ext
            ):
                ids.append(d["id"])
            elif tag == "test_report" and any(
                "测试" in c for c in contains
            ):
                ids.append(d["id"])
            elif tag == "test_report" and any(
                e == ".md" for e in ext
            ) and "测试" in (d.get("name") or ""):
                ids.append(d["id"])
    # 去重保序
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


async def extract_manifest_from_materials(
    *,
    materials_text: str,
    skill_hint: str = "",
) -> dict:
    """LLM 抽取交付清单；校验后返回归一化 dict。失败抛 DeliveryManifestError。"""
    from project_runtime.llm import (
        load_llm_settings,
        normalize_model_for_gateway,
    )
    from project_runtime.llm.provider import llm_complete

    cfg = load_llm_settings()
    if not (cfg.base_url and cfg.api_key and cfg.model):
        raise DeliveryManifestError(
            "无法抽取交付清单：LLM 未配置"
        )
    model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)
    prompt = (
        "根据下列项目材料与交付基线提示，抽出本项目最终必须交付的产物清单。\n"
        "只输出一个 JSON 对象，不要 markdown 围栏，格式严格如下：\n"
        '{"version":1,"source":"plan_extract","deliverables":[\n'
        '  {"id":"d1","name":"产物中文名","match":{"ext":[".pptx"],'
        '"min_count":1}}\n'
        "]}\n"
        "match 只允许键: ext, name_contains, any_name_prefix, min_count。"
        "代码类产物请同时设 ext 与 any_name_prefix:[\"readme\"]。\n"
        "测试报告类请 name_contains 含「测试」且 ext 含 .md。\n\n"
        f"【交付基线提示】\n{skill_hint[:1500] or '（无）'}\n\n"
        f"【材料摘要】\n{materials_text[:6000]}"
    )
    import asyncio

    result = await asyncio.wait_for(
        llm_complete(
            model=model_id,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            timeout=120,
        ),
        timeout=130,
    )
    if not result.get("ok"):
        raise DeliveryManifestError(
            f"交付清单抽取失败：{result.get('error', 'LLM error')}"
        )
    resp = result["response"]
    text = (resp.choices[0].message.content or "").strip()
    # 剥 markdown 围栏
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise DeliveryManifestError(
            f"模型未返回合法 JSON：{e}；片段：{text[:180]}"
        ) from e
    return validate_manifest(raw)


def assign_ids_heuristic(
    task_name: str,
    stage_outputs: list[str],
    delivery_tags: list[str],
    manifest: dict,
) -> list[str]:
    """把清单项分配到任务：tags 映射 + 名称/产出物子串命中。"""
    ids = map_legacy_tags_to_ids(manifest, delivery_tags)
    blob = " ".join([task_name] + list(stage_outputs or []))
    for d in manifest.get("deliverables") or []:
        name = d.get("name") or ""
        if name and name in blob and d["id"] not in ids:
            ids.append(d["id"])
        # 产出物词与 name 互相包含
        for so in stage_outputs or []:
            if so and (so in name or name in so) and d["id"] not in ids:
                ids.append(d["id"])
    return ids
