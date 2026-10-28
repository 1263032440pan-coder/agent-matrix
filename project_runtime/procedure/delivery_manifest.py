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


def extract_json_dict(text: str) -> dict:
    """从模型输出中取出清单 JSON 对象。

    容忍：markdown 围栏、前后说明、多个 ``{...}`` 碎片（选含 deliverables 的）、
    trailing Extra data（用 raw_decode 只吃第一个完整对象）。
    """
    raw = (text or "").strip()
    if not raw:
        raise DeliveryManifestError(
            "模型未返回合法 JSON：空响应"
            "（推理模型可能把 token 耗在思考上，请重试）"
        )
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()

    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidates.append(obj)

    if not candidates:
        raise DeliveryManifestError(
            f"模型未返回合法 JSON：无对象；片段：{raw[:180]}"
        )

    for obj in candidates:
        if "deliverables" in obj:
            return obj
    # 退而求其次：带 version 的根对象
    for obj in candidates:
        if "version" in obj:
            return obj
    raise DeliveryManifestError(
        "模型未返回交付清单根对象（缺 deliverables）；"
        f"片段：{raw[:180]}"
    )


def _coerce_str_list(val: Any) -> list[str] | None:
    """LLM 常把 name_contains/ext 写成单字符串——归一为非空字符串数组。

    返回 None 表示类型无法接受（非 str/list 或元素非 str）。
    """
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, list):
        out: list[str] = []
        for x in val:
            if not isinstance(x, str):
                return None
            s = x.strip()
            if s:
                out.append(s)
        return out
    return None


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
        # 空数组视为「未提供该键」（LLM 常写 "ext":[]），不直接报不可为空
        if "ext" in match:
            ext = _coerce_str_list(match["ext"])
            if ext is None:
                raise DeliveryManifestError(
                    f"deliverables[{i}].match.ext 须为字符串或字符串数组"
                )
            if ext:
                norm_match["ext"] = [
                    x.lower() if x.startswith(".") else f".{x.lower()}"
                    for x in ext
                ]
        for key in ("name_contains", "any_name_prefix"):
            if key in match:
                val = _coerce_str_list(match[key])
                if val is None:
                    raise DeliveryManifestError(
                        f"deliverables[{i}].match.{key} 须为字符串或字符串数组"
                    )
                if val:
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
        llm_message_text,
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
        "每项 match 必须至少有非空的 ext 或 name_contains（二者缺一不可都空）；"
        "ext / name_contains / any_name_prefix 必须是 JSON 字符串数组"
        "（例 name_contains:[\"测试\"]，禁止写成单个字符串）。\n"
        "代码类产物请同时设 ext 与 any_name_prefix:[\"readme\"]。\n"
        "测试报告类请 name_contains:[\"测试\"] 且 ext 含 .md。\n"
        "禁止解释字段含义（不要写「min_count 表示…」这类说明）。"
        "不要输出思考过程；JSON 之外不要有任何文字。\n\n"
        f"【交付基线提示】\n{skill_hint[:1500] or '（无）'}\n\n"
        f"【材料摘要】\n{materials_text[:6000]}"
    )
    import asyncio

    async def _once(messages: list[dict], max_tokens: int) -> str:
        result = await asyncio.wait_for(
            llm_complete(
                model=model_id,
                api_base=cfg.base_url,
                api_key=cfg.api_key,
                messages=messages,
                max_tokens=max_tokens,
                timeout=120,
            ),
            timeout=130,
        )
        if not result.get("ok"):
            raise DeliveryManifestError(
                f"交付清单抽取失败：{result.get('error', 'LLM error')}"
            )
        return llm_message_text(result["response"].choices[0].message)

    text = await _once([{"role": "user", "content": prompt}], 4000)
    if not text.strip():
        # 推理模型偶发空 content：再要一次，强制只吐 JSON
        text = await _once(
            [
                {"role": "user", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "上一次未返回正文。请立刻只输出一个合法 JSON 对象，"
                        "不要思考过程、不要 markdown、不要解释。"
                    ),
                },
            ],
            4000,
        )
    try:
        raw = extract_json_dict(text)
    except DeliveryManifestError:
        # 一次纠错：把坏输出喂回，要求只吐根对象
        text = await _once(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": (text or "")[:2000]},
                {
                    "role": "user",
                    "content": (
                        "上一段不是合法的交付清单根对象。"
                        "请只输出一个 JSON："
                        '{"version":1,"source":"plan_extract","deliverables":[...]}，'
                        "deliverables 每项含 id/name/match；"
                        "不要解释、不要单独输出 match 碎片。"
                    ),
                },
            ],
            4000,
        )
        raw = extract_json_dict(text)
    try:
        return validate_manifest(raw)
    except DeliveryManifestError as ve:
        # schema 失败再纠一次（常见：ext:[] / 缺 name_contains）
        text = await _once(
            [
                {"role": "user", "content": prompt},
                {
                    "role": "assistant",
                    "content": json.dumps(raw, ensure_ascii=False)[:2500],
                },
                {
                    "role": "user",
                    "content": (
                        f"校验失败：{ve.message}。"
                        "请输出修正后的完整清单 JSON："
                        "每项 match 须有非空 ext 或非空 name_contains；"
                        "禁止空数组；不要解释。"
                    ),
                },
            ],
            4000,
        )
        return validate_manifest(extract_json_dict(text))


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
