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

输出格式（仅输出 JSON，不要任何其他文字）：
{{
  "nodes": [
    {{
      "name": "节点名",
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

资料来源：{source_name}
资料正文：
{text}
"""


class LLMExtractor:
    """LiteLLM-backed extractor. Conforms to NormalizerExtractor protocol."""

    def __init__(self, llm_config, fallback: KeywordExtractor | None = None,
                 project_id: int | None = None):
        self._cfg = llm_config
        self._fallback = fallback or KeywordExtractor()
        self._project_id = project_id  # CoT 事件归属（None=全局）

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
        import litellm

        model = self._cfg.model
        known = ("openai/", "azure/", "anthropic/", "huggingface/", "ollama/",
                 "gemini/", "deepseek/", "mistral/")
        if "/" in model and not model.startswith(known):
            model = f"openai/{model}"

        prompt = _PROMPT.format(text=text[:12000], source_name=source_name)
        # 流式调用（CoT UI）：reasoning/content 分通道增量收集，
        # 思考过程边收边发 LLM_THINKING_CHUNK 事件供界面实时显示
        stream = await litellm.acompletion(
            model=model,
            api_base=self._cfg.base_url,
            api_key=self._cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16000,
            timeout=600,  # 推理型模型思考+生成长文档抽取——实测 104s+，600s 上限
            stream=True,
        )
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        think_buf = ""
        async for chunk in stream:
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
        if think_buf:
            await _emit_cot(self._project_id, "thinking", think_buf)
        raw = "".join(content_parts)
        # 推理型模型：content 可能为空而 reasoning_content 有内容（token 耗尽在思考）。
        # 优先 content；空则从 reasoning_content 尾部捞 JSON（思考里常已产出结构）
        if not raw.strip():
            rc_all = "".join(reasoning_parts)
            # 找思考里的 json 块
            m = re.search(r"\[[\s\S]*\]", rc_all)
            if m:
                raw = m.group(0)
        nodes = self._parse(raw)
        if not nodes:
            raise ValueError("LLM returned no parseable nodes")
        return [self._normalize_node(n, source_name) for n in nodes]

    def _parse(self, raw: str) -> list[dict]:
        # Strip code fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        nodes = data.get("nodes", [])
        return nodes if isinstance(nodes, list) else []

    def _normalize_node(self, n: dict, source_name: str) -> dict:
        node = {
            "name": str(n.get("name") or "未命名"),
            "workstream": None,
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
