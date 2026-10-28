"""LLMExtractor — LLM-based ProcedureNode extraction (batch 3 / D06-TD01).

Replaces KeywordExtractor when an LLM is configured. Falls back to keyword
extraction when the LLM is unavailable or returns invalid output.

Hard rules (WP-037 red lines):
  - LLM must NOT invent field values: missing info stays UNKNOWN.
  - Low-confidence fields are marked AMBIGUOUS, not guessed.
  - CONFLICTED: same-name nodes with conflicting field values are detected
    and flagged (not silently merged).
"""

from __future__ import annotations

import json
import logging
import re

from project_runtime.procedure.normalizer import AMBIGUOUS, UNKNOWN, KeywordExtractor

logger = logging.getLogger(__name__)

_FIELDS = [
    "inputs", "activities", "outputs", "roles", "dependencies", "gate",
    "acceptance_criteria", "evidence_requirements", "constraints", "rework_path",
]

_PROMPT = """你是项目流程分析器。从下面的方法资料中抽取流程节点列表。

要求：
1. 每个节点对应资料中一个真实的阶段/活动（不要虚构资料中不存在的阶段）。
2. 每个节点的字段只能从资料原文提取；资料没写的字段一律填 "UNKNOWN"，禁止编造。
3. 资料中表述含糊、有多种解读的字段填 "AMBIGUOUS"。
4. dependencies 字段填上游节点的 name（必须在列表中存在），无依赖填 "UNKNOWN"。
5. 节点粒度：以资料中的阶段/工作流为单位，不要把每个小标题都拆成节点；通常 3-10 个节点为宜。
6. name 字段必须是中文（V1.4f）: 资料原文为英文标题时译成中文
   （如 Project Understanding→项目理解、Task Graph→任务图构建、
   Gate→质量关卡）；中文资料照抄原文。
7. stage 字段（返修V3 批A）: 流程阶段由你读材料（及 skill 阶段指导，若有）
   **临场自由命名**——用材料/skill 里的真实阶段名（如"任务图构建"、"联调验收"），
   不要强行塞进固定格子。同一材料+不同 skill 可以产出不同但各自合理的阶段划分。
   九词默认种子（仅当材料与 skill 都给不出阶段线索时参考，不是天花板）:
   "项目理解"/"需求基线"/"解决方案"/"原型设计"/"架构设计"/"开发实现"/
   "测试验证"/"交付打包"/"使用文档"。
   确实无法命名时填 "UNKNOWN"（最后手段）；拿不准填 "AMBIGUOUS"（交人不硬选）。

输出格式（仅输出 JSON，不要任何其他文字）：
{{
  "nodes": [
    {{
      "name": "节点名",
      "stage": "项目理解",
      "inputs": "...",
      "activities": "...",
      "outputs": "...",
      "roles": "...",
      "dependencies": "...",
      "gate": "...",
      "acceptance_criteria": "...",
      "evidence_requirements": "...",
      "constraints": "...",
      "rework_path": "..."
    }}
  ]
}}

重要：完整 JSON 必须写在正式回复（content）里；思考过程不要起草整表，
更不要只写「现在输出 JSON」而不真正输出对象。

资料来源：{source_name}
资料正文：
{text}
"""

# 流式空闲超时：思考结束后若网关不再推送 chunk，避免干等到 605s
_STREAM_IDLE_SEC = 60.0
_RETRY_JSON_ONLY = (
    "上一次未在正式回复中给出合法 JSON。"
    "请立刻只输出一个 JSON 对象 "
    '{"nodes":[{"name":"...","stage":"...","inputs":"UNKNOWN",'
    '"activities":"UNKNOWN","outputs":"UNKNOWN","roles":"UNKNOWN",'
    '"dependencies":"UNKNOWN","gate":"UNKNOWN",'
    '"acceptance_criteria":"UNKNOWN","evidence_requirements":"UNKNOWN",'
    '"constraints":"UNKNOWN","rework_path":"UNKNOWN"}]}，'
    "不要思考过程、不要 markdown、不要解释。"
)


class LLMExtractor:
    """LiteLLM-backed extractor. Conforms to NormalizerExtractor protocol."""

    def __init__(self, llm_config, fallback: KeywordExtractor | None = None,
                 project_id: int | None = None,
                 delivery_profile: str | None = None):
        self._cfg = llm_config
        self._fallback = fallback or KeywordExtractor()
        self._project_id = project_id  # CoT 事件归属（None=全局）
        # V1.6 序2: SOFTWARE_DEV → prompt 注入《交付基线》约定（仅 WHAT
        # 不约束 WHERE——阶段归属 AI 自主; 不复制 skill 正文）
        self._delivery_profile = delivery_profile

    async def extract(self, text: str, source_name: str) -> list[dict]:
        from project_runtime.llm import test_llm_connection  # noqa: F401 — config guard

        if not (self._cfg.base_url and self._cfg.api_key and self._cfg.model):
            # LLM 未配置：允许关键词模式（用户明确没接大模型）
            logger.info("LLM not configured — keyword fallback")
            return await self._fallback.extract(text, source_name)

        # LLM 已配置：失败必须抛错，禁止静默降级（用户决策 2026-09-25）。
        # 降级产物（标题碎片任务）比失败更糟——它会经幂等键被永久缓存，
        # 且用户误以为大模型参与了理解。
        return await self._extract_llm(text, source_name)

    async def _extract_llm(self, text: str, source_name: str) -> list[dict]:
        import asyncio as _asyncio

        model = self._cfg.model
        known = ("openai/", "azure/", "anthropic/", "huggingface/", "ollama/",
                 "gemini/", "deepseek/", "mistral/")
        if "/" in model and not model.startswith(known):
            model = f"openai/{model}"

        prompt = _PROMPT.format(text=text[:12000], source_name=source_name)
        if self._delivery_profile == "SOFTWARE_DEV":
            prompt += (
                chr(10) + chr(10) +
                "【交付基线约定】本项目为软件开发需求，最终交付必然包含三件："
                "①项目方案介绍 PPT ②可运行的代码（多文件组织+README+依赖清单）"
                "③测试报告。三件在流程的哪个阶段、哪个任务产生，由你根据材料"
                "自行安排——本约定只约束交付完整性，不约束流程设计。"
                "承载任务的 outputs 请体现对应产物。"
            )
        # 流式 + CoT；空闲超时防网关断流；失败则 JSON-only 重试一次

        content, reasoning = await self._stream_once(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16000,
            call_timeout=600,
        )
        nodes = self._parse(_recover_nodes_json(content, reasoning))
        if not nodes:
            # 推理模型常在「现在输出 JSON」后断流/空 content——非流式再要一次
            logger.warning(
                "procedure extract: empty/unparseable after stream; "
                "retry JSON-only once"
            )
            from project_runtime.llm.provider import llm_complete
            from project_runtime.llm import llm_message_text

            retry = await _asyncio.wait_for(
                llm_complete(
                    model=model,
                    api_base=self._cfg.base_url,
                    api_key=self._cfg.api_key,
                    messages=[
                        {"role": "user", "content": prompt},
                        {"role": "user", "content": _RETRY_JSON_ONLY},
                    ],
                    max_tokens=8000,
                    timeout=180,
                ),
                timeout=190,
            )
            if not retry.get("ok"):
                raise RuntimeError(retry.get("error", "LLM retry failed"))
            text = llm_message_text(retry["response"].choices[0].message)
            await _emit_cot(self._project_id, "answer", text[:500] or "(empty)")
            nodes = self._parse(_recover_nodes_json(text, ""))
        if not nodes:
            raise ValueError("LLM returned no parseable nodes")
        return [self._normalize_node(n, source_name) for n in nodes]

    async def _stream_once(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        call_timeout: int,
    ) -> tuple[str, str]:
        """流式抽取；返回 (content, reasoning)。空闲超时提前结束消费。"""
        import asyncio as _asyncio

        from project_runtime.llm.provider import llm_acomplete

        result = await _asyncio.wait_for(
            llm_acomplete(
                model=model,
                api_base=self._cfg.base_url,
                api_key=self._cfg.api_key,
                messages=messages,
                max_tokens=max_tokens,
                timeout=call_timeout,
                stream=True,
            ),
            timeout=call_timeout + 5,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "LLM failed"))
        stream = result["response"]
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        think_buf = ""
        answer_buf = ""
        ait = stream.__aiter__()
        while True:
            try:
                chunk = await _asyncio.wait_for(
                    ait.__anext__(), timeout=_STREAM_IDLE_SEC,
                )
            except StopAsyncIteration:
                break
            except _asyncio.TimeoutError:
                logger.warning(
                    "LLM stream idle >%.0fs — stop consuming "
                    "(content=%d reasoning=%d chars)",
                    _STREAM_IDLE_SEC,
                    sum(len(x) for x in content_parts),
                    sum(len(x) for x in reasoning_parts),
                )
                await _emit_cot(
                    self._project_id,
                    "thinking",
                    f"\n[流式空闲超时 {_STREAM_IDLE_SEC:.0f}s，停止等待]\n",
                )
                break
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None) or ""
            cc = delta.content or ""
            if rc:
                reasoning_parts.append(rc)
                think_buf += rc
                if len(think_buf) >= 120 or "\n" in think_buf:
                    await _emit_cot(self._project_id, "thinking", think_buf)
                    think_buf = ""
            if cc:
                content_parts.append(cc)
                answer_buf += cc
                if len(answer_buf) >= 80 or "\n" in answer_buf:
                    await _emit_cot(self._project_id, "answer", answer_buf)
                    answer_buf = ""
        if think_buf:
            await _emit_cot(self._project_id, "thinking", think_buf)
        if answer_buf:
            await _emit_cot(self._project_id, "answer", answer_buf)
        return "".join(content_parts), "".join(reasoning_parts)

    def _parse(self, raw: str) -> list[dict]:
        raw = (raw or "").strip()
        if not raw:
            return []
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            nodes = data.get("nodes", [])
            return nodes if isinstance(nodes, list) else []
        return []

    def _normalize_node(self, n: dict, source_name: str) -> dict:
        # V1.4b 修1c: stage 字段落 workstream 列（环节贯通——
        # normalizer → ProcedureNode.workstream → builder ws → planner
        # task_type 三源链的源头。UNKNOWN/缺失不写（落 None 走归组兜底）
        stage = str(n.get("stage") or "").strip()
        node = {
            "name": str(n.get("name") or "未命名"),
            "workstream": (
                stage if stage and stage not in ("UNKNOWN", "AMBIGUOUS")
                else None
            ),
            "readiness_status": "MISSING",
            "source_ref": source_name,
        }
        filled = 0
        for f in _FIELDS:
            v = n.get(f)
            if isinstance(v, str) and v.strip() and v.strip() != UNKNOWN:
                node[f] = v.strip()
                filled += 1
            else:
                node[f] = UNKNOWN

        has_ambiguous = any(node[f] == AMBIGUOUS for f in _FIELDS)
        if filled == 0:
            node["readiness_status"] = "MISSING"
        elif has_ambiguous:
            node["readiness_status"] = "AMBIGUOUS"
        elif filled >= len(_FIELDS):
            node["readiness_status"] = "READY"
        else:
            node["readiness_status"] = "PARTIAL"
        return node


def _recover_nodes_json(content: str, reasoning: str) -> str:
    """优先 content；空则从 reasoning 捞 {\"nodes\":...} 或节点数组。"""
    for blob in (content or "", reasoning or ""):
        text = blob.strip()
        if not text:
            continue
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        m = re.search(r"\{[\s\S]*\"nodes\"[\s\S]*\}", text)
        if m:
            return m.group(0)
        m = re.search(
            r'\[\s*\{\s*"name"[\s\S]*\}\s*\]', text,
        )
        if m:
            return m.group(0)
        if text.lstrip().startswith(("{", "[")):
            return text
    return (content or "").strip()


def detect_conflicts(nodes: list[dict]) -> list[dict]:
    """D06-TD01: same-name nodes with differing field values → CONFLICTED.

    Mutates nodes in place (marks readiness_status=CONFLICTED) and returns
    the list of conflicted node names.
    """
    by_name: dict[str, list[dict]] = {}
    for n in nodes:
        by_name.setdefault(n["name"], []).append(n)

    conflicted: list[str] = []
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        for f in _FIELDS:
            values = {g[f] for g in group if g[f] not in (UNKNOWN, AMBIGUOUS)}
            if len(values) > 1:
                conflicted.append(name)
                for g in group:
                    g["readiness_status"] = "CONFLICTED"
                break
    return conflicted


async def _emit_cot(project_id: int | None, phase: str, text: str) -> None:
    """思维链增量 → 事件（best-effort，绝不打断抽取主链）。"""
    try:
        from project_runtime.event import EventEngine, RuntimeEventType

        evt = (
            RuntimeEventType.LLM_THINKING_CHUNK
            if phase == "thinking"
            else RuntimeEventType.LLM_ANSWER_CHUNK
        )
        await EventEngine().record(
            evt,
            project_id=project_id,
            phase=f"LLM_{phase.upper()}",
            message=text[-128:],
            actor="LLM",
            payload={"text": text},
        )
    except Exception:  # noqa: BLE001 — 显示链路绝不影响执行
        pass
