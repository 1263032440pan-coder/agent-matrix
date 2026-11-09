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
    "slot", "rel_dir", "code_scan",
})

# SYS-134: 文档类针 → 类型槽（约定相对目录，不验中英文件名）
NEEDLE_TO_SLOT: dict[str, str] = {
    "需求": "requirements",
    "架构": "architecture",
    "原型": "prototype",
    "流程图": "flowchart",
    "解决方案": "solution",
    "开发": "development",
    "测试": "testing",
    "理解": "understanding",
}
_SLOT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# SYS-128: 源码类纯压缩包 → 改写为默认源码后缀（不含 .md：说明文档不能冒充源码）
DEFAULT_SOURCE_EXTS: tuple[str, ...] = (
    ".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".toml", ".json",
)
_DOC_ONLY_EXTS = frozenset({".md", ".txt", ".rst", ".adoc"})
_ARCHIVE_EXTS = frozenset({
    ".zip", ".tar", ".tar.gz", ".tgz", ".7z", ".rar",
})
_SOURCE_NAME_RE = re.compile(
    r"源码|源代码|代码|\bcode\b|\bsource\b", re.I,
)
_RELEASE_NAME_RE = re.compile(
    r"安装包|发行包|发布包|release|installer|安装程序", re.I,
)

# SYS-133: 等价媒体/图类多选 → 单一主后缀（令牌 Stop 合取前收束）
BITMAP_EXT_PRIORITY: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
)
_BITMAP_EXTS = frozenset(BITMAP_EXT_PRIORITY)
_SVG_EXT = ".svg"
_PDF_EXT = ".pdf"


def is_source_like_item(name: str, match: dict | None = None) -> bool:
    """名称/针像源码，且不是明确发行包。"""
    blob = name or ""
    if match:
        for n in match.get("name_contains") or []:
            blob += " " + str(n)
    if _RELEASE_NAME_RE.search(blob):
        return False
    return bool(_SOURCE_NAME_RE.search(blob))


def _coerce_source_archive_exts(
    name: str, norm_match: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """源码类纯/混压缩 ext → 改写；返回 (match, coerced)."""
    if not is_source_like_item(name, norm_match):
        return norm_match, False
    exts = list(norm_match.get("ext") or [])
    if not exts:
        return norm_match, False
    archives = [e for e in exts if e in _ARCHIVE_EXTS or e.endswith(".gz")]
    # also treat .tar.gz already normalized as single token
    code = [e for e in exts if e not in _ARCHIVE_EXTS]
    if not archives:
        return norm_match, False
    out = dict(norm_match)
    if not code:
        out["ext"] = list(DEFAULT_SOURCE_EXTS)
        return out, True
    out["ext"] = code
    return out, True


def coerce_equivalent_media_exts(
    norm_match: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """位图多选或位图+pdf/svg → 收束为单一主后缀；返回 (match, coerced)."""
    exts = list(norm_match.get("ext") or [])
    if len(exts) <= 1:
        return norm_match, False
    bitmap = [e for e in exts if e in _BITMAP_EXTS]
    has_pdf = _PDF_EXT in exts
    has_svg = _SVG_EXT in exts
    if not (
        len(bitmap) >= 2
        or (len(bitmap) >= 1 and has_pdf)
        or (len(bitmap) >= 1 and has_svg)
    ):
        return norm_match, False
    primary = next(
        (p for p in BITMAP_EXT_PRIORITY if p in bitmap),
        bitmap[0],
    )
    out = dict(norm_match)
    out["ext"] = [primary]
    return out, True


def default_rel_dir(slot: str) -> str:
    return f"deliverables/{slot}"


def infer_slot_from_text(blob: str) -> str | None:
    """从名称/针文本推断类型槽；先长短语后单词。"""
    text = blob or ""
    # longer needles first
    for needle in sorted(NEEDLE_TO_SLOT.keys(), key=len, reverse=True):
        if needle in text:
            return NEEDLE_TO_SLOT[needle]
    lower = text.lower()
    for needle, slot in NEEDLE_TO_SLOT.items():
        if slot in lower.replace("-", "_"):
            return slot
    return None


def coerce_source_code_match(
    name: str, norm_match: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """源码槽/源码类项：去掉仅文档后缀，开启全树 code_scan（backend/src 等可命中）。

    避免 deliverables/source_code/*.md 冒充「项目源码已齐」。
    """
    slot = str(norm_match.get("slot") or "").strip().lower()
    if slot != "source_code" and not is_source_like_item(name, norm_match):
        return norm_match, False
    out = dict(norm_match)
    coerced = False
    exts = [str(e).lower() for e in (out.get("ext") or [])]
    kept = [e for e in exts if e not in _DOC_ONLY_EXTS]
    if not kept:
        out["ext"] = list(DEFAULT_SOURCE_EXTS)
        coerced = True
    elif kept != exts:
        out["ext"] = kept
        coerced = True
    if not out.get("code_scan"):
        out["code_scan"] = True
        coerced = True
    return out, coerced


def coerce_doc_slot(
    name: str, norm_match: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """文档类 name_contains/名称 → slot+rel_dir；有槽则去掉 name_contains。"""
    out = dict(norm_match)
    coerced = False
    slot = str(out.get("slot") or "").strip().lower().replace("-", "_")
    if slot and not _SLOT_RE.match(slot):
        slot = ""
    if not slot:
        blob = name or ""
        for n in out.get("name_contains") or []:
            blob += " " + str(n)
        slot = infer_slot_from_text(blob) or ""
        if slot:
            coerced = True
    if not slot:
        return norm_match, False
    out["slot"] = slot
    rel = str(out.get("rel_dir") or "").strip().replace("\\", "/")
    rel = rel.strip("/")
    expected = default_rel_dir(slot)
    if not rel:
        out["rel_dir"] = expected
        coerced = True
    else:
        out["rel_dir"] = rel
    if "name_contains" in out:
        del out["name_contains"]
        coerced = True
    return out, coerced


def path_in_rel_dir(path_str: str, rel_dir: str) -> bool:
    """相对路径是否落在槽目录下（允许任务/part 等前缀嵌套）。

    例：``deliverables/requirements/a.md``、
    ``1/deliverables/requirements/a.md``、
    ``part_0/deliverables/requirements/a.md`` 均命中。
    """
    p = str(path_str or "").replace("\\", "/").strip("/")
    root = str(rel_dir or "").replace("\\", "/").strip("/")
    if not p or not root:
        return False
    pl, rl = p.lower(), root.lower()
    if pl == rl or pl.startswith(rl + "/"):
        return True
    # 项目根扫描：路径形如 {task_id}/deliverables/{slot}/...
    return ("/" + pl + "/").find("/" + rl + "/") >= 0


class DeliveryManifestError(ValueError):
    """清单 JSON/schema 非法——规划应中止并 toast。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.error_code = ERROR_CODE
        self.message = message


_META_PROSE_RE = re.compile(
    r"(?is)\b("
    r"we need|need ensure|need produce|need continue|need be careful|"
    r"answer only\s+json|only json object|let me think|i will|"
    r"i'll|continue from previous|produce deliverables"
    r")\b"
)


def looks_like_meta_prose(text: str) -> bool:
    """英文自检/续写指令冒充正文（无 deliverables JSON）。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if '"deliverables"' in raw or "'deliverables'" in raw:
        return False
    if "{" not in raw:
        return True
    return bool(_META_PROSE_RE.search(raw))


def software_dev_baseline_manifest() -> dict:
    """SOFTWARE_DEV 三件基线——LLM 抽清单彻底失败时的确定性回落。"""
    return validate_manifest({
        "version": 1,
        "source": "baseline_fallback",
        "deliverables": [
            {
                "id": "d_ppt",
                "name": "方案介绍PPT",
                "match": {"ext": [".pptx"], "min_count": 1},
            },
            {
                "id": "d_code",
                "name": "可运行代码",
                "match": {
                    "ext": [".py", ".ts", ".js", ".md"],
                    "any_name_prefix": ["readme"],
                    "min_count": 1,
                },
            },
            {
                "id": "d_test",
                "name": "测试报告",
                "match": {
                    "slot": "testing",
                    "rel_dir": "deliverables/testing",
                    "ext": [".md"],
                    "min_count": 1,
                },
            },
        ],
    })


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
        if isinstance(obj.get("deliverables"), list):
            return obj
    # 退而求其次：带 version 的根对象（仍须后续 validate 有 deliverables）
    for obj in candidates:
        if "version" in obj and "deliverables" in obj:
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
        if "slot" in match and match["slot"] is not None:
            raw_slot = match["slot"]
            if not isinstance(raw_slot, str):
                raise DeliveryManifestError(
                    f"deliverables[{i}].match.slot 须为字符串"
                )
            slot_s = raw_slot.strip().lower().replace("-", "_")
            if slot_s:
                if not _SLOT_RE.match(slot_s):
                    raise DeliveryManifestError(
                        f"deliverables[{i}].match.slot 非法：{raw_slot!r}"
                    )
                norm_match["slot"] = slot_s
        if "rel_dir" in match and match["rel_dir"] is not None:
            if not isinstance(match["rel_dir"], str):
                raise DeliveryManifestError(
                    f"deliverables[{i}].match.rel_dir 须为字符串"
                )
            rd = match["rel_dir"].strip().replace("\\", "/").strip("/")
            if rd:
                norm_match["rel_dir"] = rd
        if "code_scan" in match and match["code_scan"] is not None:
            raw_scan = match["code_scan"]
            if not isinstance(raw_scan, bool):
                raise DeliveryManifestError(
                    f"deliverables[{i}].match.code_scan 须为布尔值"
                )
            if raw_scan:
                norm_match["code_scan"] = True
        min_count = int(match.get("min_count") or 1)
        if min_count < 1:
            raise DeliveryManifestError(
                f"deliverables[{i}].match.min_count 须 ≥ 1"
            )
        norm_match["min_count"] = min_count
        if not any(k in norm_match for k in (
            "ext", "name_contains", "slot",
        )):
            raise DeliveryManifestError(
                f"deliverables[{i}].match 至少须有 ext、name_contains 或 slot"
            )
        norm_match, coerced_archive = _coerce_source_archive_exts(
            name, norm_match,
        )
        norm_match, coerced_media = coerce_equivalent_media_exts(norm_match)
        norm_match, coerced_slot = coerce_doc_slot(name, norm_match)
        norm_match, coerced_source = coerce_source_code_match(
            name, norm_match,
        )
        if not any(k in norm_match for k in ("ext", "name_contains", "slot")):
            raise DeliveryManifestError(
                f"deliverables[{i}].match 纠偏后仍缺 ext/name_contains/slot"
            )
        row: dict[str, Any] = {
            "id": did, "name": name, "match": norm_match,
        }
        if coerced_archive:
            row["coerced_from_archive"] = True
            logger.info(
                "manifest coerce source archive → code exts id=%s name=%s",
                did, name,
            )
        if coerced_media:
            row["coerced_ext_singleton"] = True
            logger.info(
                "manifest coerce media exts → singleton id=%s name=%s ext=%s",
                did, name, norm_match.get("ext"),
            )
        if coerced_slot:
            row["coerced_to_slot"] = True
            logger.info(
                "manifest coerce → slot id=%s name=%s slot=%s rel_dir=%s",
                did, name, norm_match.get("slot"), norm_match.get("rel_dir"),
            )
        if coerced_source:
            row["coerced_source_code"] = True
            logger.info(
                "manifest coerce source_code strip-doc+scan id=%s name=%s "
                "ext=%s",
                did, name, norm_match.get("ext"),
            )
        normalized.append(row)
    return {
        "version": int(data.get("version") or 1),
        "source": str(data.get("source") or "plan_extract"),
        "deliverables": normalized,
    }


def _ext_hit(path: str, exts: list) -> bool:
    name = Path(str(path).replace("\\", "/")).name.lower()
    return any(name.endswith(str(e).lower()) for e in exts)


def list_match_hits(file_names: list[str], match: dict) -> list[str]:
    """返回满足 match 主条件（slot/ext/name_contains）的相对路径列表。

    ``code_scan``：源码项可在全树按扩展名命中（backend/src 等），
    不只认 deliverables/source_code/*.md。
    """
    names = list(file_names)
    slot = match.get("slot")
    exts = list(match.get("ext") or [])
    code_scan = bool(match.get("code_scan")) or slot == "source_code"

    if code_scan and exts:
        # 全树代码文件；排除纯文档后缀冒充
        code_exts = [e for e in exts if str(e).lower() not in _DOC_ONLY_EXTS]
        if not code_exts:
            code_exts = list(DEFAULT_SOURCE_EXTS)
        hits = [n for n in names if _ext_hit(str(n), code_exts)]
        return hits

    hits = names
    rel_dir = match.get("rel_dir") or (
        default_rel_dir(str(slot)) if slot else ""
    )
    if slot:
        hits = [n for n in hits if path_in_rel_dir(str(n), rel_dir)]
    if exts:
        hits = [n for n in hits if _ext_hit(str(n), exts)]
    if not slot and "name_contains" in match:
        needles = match["name_contains"]
        hits = [
            n for n in hits
            if any(
                nd in Path(str(n).replace("\\", "/")).name
                for nd in needles
            )
        ]
    return hits


def check_match(file_names: list[str], match: dict) -> list[str]:
    """对一组文件名（可为相对路径）做 match 检查；返回问题文案（空=通过）。"""
    names = list(file_names)
    basenames_lower = [
        Path(str(n).replace("\\", "/")).name.lower() for n in names
    ]
    problems: list[str] = []
    min_count = int(match.get("min_count") or 1)

    primary = list_match_hits(names, match)
    if len(primary) < min_count:
        bits = []
        if match.get("code_scan") or match.get("slot") == "source_code":
            bits.append("真实源码文件（backend/src/或源码槽，不含仅 .md）")
        elif match.get("slot"):
            bits.append(
                f"槽目录 {match.get('rel_dir') or default_rel_dir(match['slot'])}/"
            )
        if match.get("ext"):
            bits.append(f"扩展名 {match['ext']}")
        if not match.get("slot") and match.get("name_contains"):
            bits.append(f"文件名含 {match['name_contains']}")
        problems.append(
            f"缺少满足条件的文件（需 ≥{min_count}）："
            + "、".join(bits)
        )
    if "any_name_prefix" in match:
        prefixes = [p.lower() for p in match["any_name_prefix"]]
        if not any(
            any(n.startswith(p) for p in prefixes) for n in basenames_lower
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


def project_workspace_rel_paths(
    project_id: int,
    *,
    limit: int = 4000,
    root: Path | None = None,
) -> list[str]:
    """项目工作区相对路径列表（data/tasks/{id}），供清单实时对比。"""
    base = root if root is not None else Path(f"data/tasks/{int(project_id)}")
    if not base.exists():
        return []
    out: list[str] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
        out.append(rel)
        if len(out) >= limit:
            break
    return out


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


def manifest_skill_hint_for_profile(profile: str | None) -> str:
    """交付画像 → 清单抽取用的基线提示（非 skill 包正文）。

    SOFTWARE_DEV：三件必含（PPT/代码/测试报告），材料只增不减。
    勿再写「不要编造材料未要求的项」——会冲掉 .pptx 基线。
    """
    if (profile or "").strip() != "SOFTWARE_DEV":
        return ""
    return (
        "本项目为软件开发需求，交付清单【必含】三件基线："
        "①方案介绍 PPT（match.ext 含 .pptx）；"
        "②可运行代码（源码后缀 + any_name_prefix:[\"readme\"]）；"
        "③测试报告（slot:testing 或文件名含「测试」的 .md）。"
        "材料/阶段若另有文档、图示等，可追加；"
        "不得因材料未点名 PPT 而省略 .pptx 项。"
    )


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
        "match 允许键: ext, name_contains, any_name_prefix, min_count, slot, rel_dir。"
        "每项 match 必须至少有非空的 ext、name_contains 或 slot；"
        "文档/图示类（需求/架构/原型/流程图/解决方案/开发/测试/理解等）"
        "优先写 slot（snake_case）与 rel_dir（默认 deliverables/{slot}），"
        "例如 slot:\"requirements\", rel_dir:\"deliverables/requirements\"；"
        "验收看槽目录内是否有文件，不依赖中英文件名子串。"
        "ext / name_contains / any_name_prefix 若出现必须是 JSON 字符串数组"
        "（禁止写成单个字符串）。\n"
        "代码类产物请同时设 ext 与 any_name_prefix:[\"readme\"]。\n"
        "测试报告类可 slot:\"testing\" 或 name_contains:[\"测试\"]，ext 含 .md。\n"
        "【重要】项目源码/源代码/代码：ext 须含源码后缀"
        "（如 .py/.ts/.js/.go/.java/.rs/.md），可同时 any_name_prefix:[\"readme\"]；"
        "禁止把整项 ext 写成仅 [\".zip\"]。"
        "仅发行包/安装包/发布包/交付压缩包才允许 .zip/.tar.gz 为主要 ext；"
        "材料只要可运行源码时，不得用 zip 项替代源码项。\n"
        "禁止解释字段含义（不要写「min_count 表示…」这类说明）。"
        "不要输出思考过程；JSON 之外不要有任何文字。\n\n"
        f"【交付基线提示】\n{skill_hint[:1500] or '（无）'}\n\n"
        f"【材料摘要】\n{materials_text[:6000]}"
    )
    import asyncio

    async def _once(
        messages: list[dict],
        max_tokens: int,
        *,
        json_mode: bool = False,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model_id,
            "api_base": cfg.base_url,
            "api_key": cfg.api_key,
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": 120,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        result = await asyncio.wait_for(
            llm_complete(**kwargs),
            timeout=130,
        )
        if not result.get("ok"):
            # 网关拒 json_object 时降级再试一次
            if json_mode:
                kwargs.pop("response_format", None)
                result = await asyncio.wait_for(
                    llm_complete(**kwargs),
                    timeout=130,
                )
            if not result.get("ok"):
                raise DeliveryManifestError(
                    f"交付清单抽取失败：{result.get('error', 'LLM error')}"
                )
        return llm_message_text(result["response"].choices[0].message)

    def _maybe_baseline(err: Exception) -> dict:
        """SOFTWARE_DEV 有基线提示时，LLM 仍失败 → 确定性三件，不阻断规划。"""
        if not (skill_hint or "").strip():
            raise err
        if "三件基线" not in skill_hint and "pptx" not in skill_hint.lower():
            raise err
        logger.warning(
            "delivery manifest LLM failed (%s); using SOFTWARE_DEV baseline",
            err,
        )
        return software_dev_baseline_manifest()

    hard_fix = (
        "停止自检与英文思考。你的整段回复必须是一个 JSON 对象，"
        "第一个非空白字符必须是 { ，最后一个是 }。"
        "禁止 markdown、禁止解释、禁止 We need / Need ensure 这类句子。\n"
        "固定形状：\n"
        '{"version":1,"source":"plan_extract","deliverables":['
        '{"id":"d_ppt","name":"方案介绍PPT",'
        '"match":{"ext":[".pptx"],"min_count":1}},'
        '{"id":"d_code","name":"可运行代码",'
        '"match":{"ext":[".py",".md"],"any_name_prefix":["readme"],'
        '"min_count":1}},'
        '{"id":"d_test","name":"测试报告",'
        '"match":{"slot":"testing","ext":[".md"],"min_count":1}}'
        "]}\n"
        "可按材料增删项，但必须保留 deliverables 数组。"
    )

    text = await _once([{"role": "user", "content": prompt}], 4000)
    if not text.strip() or looks_like_meta_prose(text):
        # 推理模型偶发空 content / 英文自检：强制 JSON
        text = await _once(
            [
                {"role": "user", "content": prompt},
                {"role": "user", "content": hard_fix},
            ],
            4000,
            json_mode=True,
        )
    try:
        raw = extract_json_dict(text)
    except DeliveryManifestError as e1:
        # 一次纠错：坏输出 + 硬约束 + json_mode
        text = await _once(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": (text or "")[:1500]},
                {
                    "role": "user",
                    "content": (
                        "上一段不是合法的交付清单根对象（缺 deliverables 或非 JSON）。\n"
                        + hard_fix
                    ),
                },
            ],
            4000,
            json_mode=True,
        )
        try:
            raw = extract_json_dict(text)
        except DeliveryManifestError as e2:
            return _maybe_baseline(e2 if text.strip() else e1)
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
                        "每项 match 须有非空 ext、name_contains 或 slot；"
                        "文档类优先 slot+rel_dir（deliverables/{{slot}}）；"
                        "禁止空数组；不要解释。"
                    ),
                },
            ],
            4000,
            json_mode=True,
        )
        try:
            return validate_manifest(extract_json_dict(text))
        except DeliveryManifestError as e3:
            return _maybe_baseline(e3)


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
