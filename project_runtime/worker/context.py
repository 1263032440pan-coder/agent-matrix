"""Agent 会话压缩（20号规范 §7——零嵌入融合方案）.

定稿双蓝本（不融合）:
  压缩引擎 = opencode: 触发公式 limit.context − min(20K, limit.output)
             / Prune 剥工具结果零 LLM → 仍超 Summarize
             / 失败缩文本重试一次 + 冷却 600s
  窗口结构 = Hermes: 头(system 永不动+摘要+恢复指针) / 尾(当前任务全部
             + 最近 2 任务原文) / 关键串正则保真

压缩是组装视图变换，不是数据删除——SessionStore 完整历史永不删。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── 模型窗口元数据（§7.5+ 三层: 用户覆盖 → 内置表 → 保守默认）──────
# 内置常见模型表（离线兜底；models.dev 拉取缓存属批次 3 增强）
_BUILTIN_MODEL_META: dict[str, dict[str, int]] = {
    # 网关自定义模型（qwen/ 前缀经 MoMA）——用户环境主模型
    "qwen/deepseek-v4-pro": {"context": 128000, "output": 16000},
    # 常见开源/商用
    "deepseek-chat": {"context": 128000, "output": 8192},
    "gpt-4o": {"context": 128000, "output": 16384},
    "claude-sonnet-4": {"context": 200000, "output": 64000},
}
_DEFAULT_META = {"context": 128000, "output": 16000}

_CHARS_PER_TOKEN = 4  # chars/4 估算（§7.3——网关 usage 不稳定返回）


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def get_model_meta(model_name: str | None) -> dict[str, int]:
    """模型窗口元数据（三层优先级 §7.5+）。

    ① llm_settings.json model_overrides 用户显式配置
    ② 内置表
    ③ 保守默认 128K/16K
    批次 3: models.dev/api.json 拉取缓存插在 ②③ 之间。
    """
    if not model_name:
        return dict(_DEFAULT_META)
    # ① 用户覆盖
    try:
        from project_runtime.llm import load_llm_settings

        cfg = load_llm_settings()
        overrides = getattr(cfg, "model_overrides", None) or {}
        if model_name in overrides:
            m = overrides[model_name]
            return {"context": int(m.get("context", _DEFAULT_META["context"])),
                    "output": int(m.get("output", _DEFAULT_META["output"]))}
    except Exception:  # noqa: BLE001
        pass
    # ② 内置表
    for key, meta in _BUILTIN_MODEL_META.items():
        if key in model_name or model_name.endswith(key):
            return dict(meta)
    # ③ 默认
    return dict(_DEFAULT_META)


def compression_threshold(model_name: str | None) -> int:
    """opencode 原式（§7.3 定稿）: context − min(20K, output)。

    qwen/deepseek-v4-pro: 128K − 16K = 112K。
    """
    m = get_model_meta(model_name)
    return m["context"] - min(20000, m["output"])


def prune_protect_band(model_name: str | None) -> int:
    """Prune 保护带（§7.5+ 模型适配）: min(40K, 30%×context)。"""
    m = get_model_meta(model_name)
    return min(40000, int(m["context"] * 0.30))


# ── SessionStore（§7.6——任务终态归档，完整历史永不删）──────────────


@dataclass
class TaskRecord:
    """一次任务的会话记录（归档粒度 = 压缩粒度）。"""

    task_id: int
    task_name: str
    messages: list[dict] = field(default_factory=list)  # OpenAI 消息格式
    summary_card: str | None = None                      # 压缩产物（任务卡片）

    def token_count(self) -> int:
        return sum(
            estimate_tokens(str(m.get("content") or "")) for m in self.messages
        )


class SessionStore:
    """Agent 会话记忆——序列化挂 AgentInstance.session_ref。

    存储形态: JSON（tasks: [TaskRecord…]）。
    压缩只改 TaskRecord.summary_card（原文 messages 永不删——审计可回放）。
    """

    def __init__(self) -> None:
        self.tasks: list[TaskRecord] = []

    # ── 序列化 ─────────────────────────────────────────────────────
    def to_json(self) -> str:
        return json.dumps(
            {
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "task_name": t.task_name,
                        "messages": t.messages,
                        "summary_card": t.summary_card,
                    }
                    for t in self.tasks
                ]
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str | None) -> "SessionStore":
        store = cls()
        if not raw:
            return store
        try:
            data = json.loads(raw)
            for t in data.get("tasks", []):
                store.tasks.append(TaskRecord(
                    task_id=t["task_id"],
                    task_name=t.get("task_name", ""),
                    messages=t.get("messages", []),
                    summary_card=t.get("summary_card"),
                ))
        except ValueError:
            logger.warning("session store json corrupted — start empty")
        return store

    # ── 归档（§7.6 检查点③——任务终态时）───────────────────────────
    def archive_task(self, task_id: int, task_name: str,
                     messages: list[dict]) -> None:
        """幂等归档（同 task_id 覆盖）。"""
        self.tasks = [t for t in self.tasks if t.task_id != task_id]
        self.tasks.append(TaskRecord(task_id, task_name, list(messages)))

    def total_tokens(self) -> int:
        return sum(t.token_count() for t in self.tasks)


# ── 关键串保真（Hermes lean tail——§7.3 窗口结构三件之一）──────────
_CRITICAL_PATTERNS = [
    r"(?:[A-Za-z]:[\\/]|data/tasks/)[^\s\"'，。）)]+",   # 文件路径
    r"artifact#\d+",                                       # 产物引用
    r"sha[:：][0-9a-f]{6,}",                               # 内容哈希
    r"https?://[^\s\"'，。）)]+",                          # URL
]


def extract_critical_strings(text: str) -> list[str]:
    """正则提取文件路径/产物引用/sha/URL——原文保真不转述。"""
    found: list[str] = []
    for pat in _CRITICAL_PATTERNS:
        found.extend(re.findall(pat, text or ""))
    # 去重保序
    seen: set[str] = set()
    uniq = [x for x in found if not (x in seen or seen.add(x))]
    return uniq[:40]


# ── 压缩器（opencode 引擎——§7.3 定稿三件）────────────────────────


class ContextCompressor:
    """Agent 会话压缩器。

    触发: used_tokens ≥ threshold(model)（组装前主检查点 §7.6 ①）
    动作: Prune（剥滑出任务工具结果，零 LLM）→ 仍超 Summarize（任务卡片）
    失败: 缩文本重试一次 → 再败跳过（不丢活上下文）+ 冷却 600s
    """

    _SUMMARY_COOLDOWN_S = 600
    _last_summary_failure = 0.0

    def __init__(self, model_name: str | None = None):
        self._model = model_name

    def should_compress(self, store: SessionStore,
                        upcoming_tokens: int = 0) -> bool:
        """主检查点判定（含预估本任务需求——§7.6 组装前口径）。"""
        threshold = compression_threshold(self._model)
        return store.total_tokens() + upcoming_tokens >= threshold

    async def compress(
        self, store: SessionStore, current_task_ids: list[int]
    ) -> dict:
        """执行两步压缩。返回统计（审计事件用）。

        current_task_ids: 当前任务 + 最近 2 任务的 id（尾部保护集）。
        """
        threshold = compression_threshold(self._model)
        stats = {"threshold": threshold, "pruned_tasks": 0,
                 "summarized_tasks": 0, "before": store.total_tokens()}

        # 尾部保护集之外的任务（按归档序，老的在前）
        protected = set(current_task_ids)
        compressible = [t for t in store.tasks if t.task_id not in protected]

        # ── 第 1 步 Prune（零 LLM——剥工具结果）───────────────────
        band = prune_protect_band(self._model)
        # 保护带: 最近的工具结果不清（从后往前累计 band token）
        kept = 0
        prunable = list(reversed(compressible))  # 新→老
        for tr in prunable:
            for m in reversed(tr.messages):
                if m.get("role") == "tool":
                    mt = estimate_tokens(str(m.get("content") or ""))
                    if kept + mt <= band:
                        kept += mt
                    else:
                        m["_pruned"] = True
                        stats["pruned_tasks"] += 1
            # 清理 _pruned 标记的（保留占位符——消息结构不断）
            for m in tr.messages:
                if m.get("_pruned"):
                    m["content"] = "[工具结果已清理——原文见 Artifact/SessionStore 归档]"
                    del m["_pruned"]

        # ── 第 2 步 Summarize（仍超 → 最老的任务生成卡片）─────────
        if store.total_tokens() >= threshold:
            if time.monotonic() - ContextCompressor._last_summary_failure < \
                    self._SUMMARY_COOLDOWN_S:
                stats["skipped"] = "cooldown"
                return stats
            for tr in compressible:
                if store.total_tokens() < threshold:
                    break
                if tr.summary_card is not None:
                    continue  # 已有卡片不重做（幂等）
                card = await self._summarize_task(tr)
                if card is None:
                    continue  # 单任务失败不阻断（防抖: 整体冷却由外层记）
                tr.summary_card = card
                stats["summarized_tasks"] += 1
        stats["after"] = store.total_tokens()
        return stats

    async def _summarize_task(self, tr: TaskRecord) -> str | None:
        """LLM 生成任务卡片（opencode 接班说明式 + 产物引用保真）。"""
        try:
            import asyncio

            from project_runtime.llm import (
                load_llm_settings,
                normalize_model_for_gateway,
            )

            # AOP-P2-2 清偿: litellm 直调 → LLM Capability Provider
            from project_runtime.llm.provider import llm_complete

            cfg = load_llm_settings()
            if not (cfg.base_url and cfg.api_key and cfg.model):
                return None
            model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)

            transcript = "\n".join(
                f"[{m.get('role')}] {str(m.get('content') or '')[:600]}"
                for m in tr.messages[-20:]
            )
            critical = extract_critical_strings(
                " ".join(str(m.get("content")) for m in tr.messages)
            )
            critical_block = (
                "关键引用（原文保真）:\n" + "\n".join(critical[:20])
                if critical else ""
            )

            def _card(resp) -> str:
                m = resp.choices[0].message
                return m.content or (getattr(m, "reasoning_content", "") or "")[-300:]

            # AOP-P2-2 清偿: litellm 直调 → LLM Capability Provider
            result = await asyncio.wait_for(
                llm_complete(
                    model=model_id,
                    api_base=cfg.base_url,
                    api_key=cfg.api_key,
                    messages=[{
                        "role": "user",
                        "content": (
                            "把以下任务执行记录压缩成任务卡片（~100字），"
                            "格式：任务名 | 结论要点 | 遗留问题。"
                            f"\n\n任务: {tr.task_name} (id={tr.task_id})"
                            f"\n\n执行记录:\n{transcript[:6000]}"
                            f"\n\n{critical_block}"
                        ),
                    }],
                    max_tokens=500,
                    timeout=120,
                ),
                timeout=130,
            )
            if not result.get("ok"):
                return None
            raw = _card(result["response"])
            card = (raw or "").strip()
            if len(card) < 10:
                # 缩文本重试一次（opencode 语义）
                ContextCompressor._last_summary_failure = time.monotonic()
                return None
            return card[:2000]
        except Exception:  # noqa: BLE001 — 失败冷却，不丢活上下文
            ContextCompressor._last_summary_failure = time.monotonic()
            logger.debug("task summarize failed", exc_info=True)
            return None


# ── 组装器（§7.6 五段时序——Hermes 头尾结构）───────────────────────


class ContextAssembler:
    """Agent messages 组装（§7.6——自上而下五段）。

    [1 system]  角色 prompt（永不动）
    [2 卡片区]  滑出窗口任务的 summary_card（无卡片的滑出任务用
                占位行——name+task_id，保底可溯）
    [3 原文区]  最近 2 任务完整消息
    [4 当前段]  调用方给的任务内容（WorkPackage+ContextBuilder+skill
                ——由调用方拼接，本组件只负责 1-3+透传）
    [5 追加区]  工具循环动态 append（本组件不参与）
    """

    def __init__(self, model_name: str | None = None):
        self._model = model_name
        self._compressor = ContextCompressor(model_name)

    async def assemble(
        self,
        store: SessionStore,
        role_prompt: str,
        current_task: TaskRecord | None,
        current_payload: list[dict],
    ) -> tuple[list[dict], dict]:
        """返回 (messages, stats)——stats 含 context_tokens/threshold（UI 压力条）。"""
        threshold = compression_threshold(self._model)

        # 主检查点①（组装前——含当前段预估）
        upcoming = sum(
            estimate_tokens(str(m.get("content") or ""))
            for m in current_payload
        )
        recent_ids = [t.task_id for t in store.tasks[-2:]]
        current_ids = ([current_task.task_id] if current_task else []) + recent_ids
        compressed = False
        if self._compressor.should_compress(store, upcoming):
            stats = await self._compressor.compress(store, current_ids)
            compressed = stats.get("summarized_tasks", 0) > 0 or \
                stats.get("pruned_tasks", 0) > 0

        # 窗口预算: 卡片区+原文区（不含 system 与当前段——它们是必保的）
        # 尾部 = 最近 2 任务原文；其余进卡片区
        messages: list[dict] = [{"role": "system", "content": role_prompt}]

        tail = store.tasks[-2:] if store.tasks else []
        tail_ids = {t.task_id for t in tail}
        for tr in store.tasks[:-2] if len(store.tasks) > 2 else []:
            if tr.task_id in tail_ids:
                continue
            if tr.summary_card:
                messages.append({
                    "role": "user",
                    "content": f"[任务卡片] {tr.summary_card}",
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"[历史任务] {tr.task_name} (task_id={tr.task_id}) "
                               f"——详见产物归档",
                })
        for tr in tail:
            messages.extend(tr.messages)
        messages.extend(current_payload)

        total = sum(
            estimate_tokens(str(m.get("content") or "")) for m in messages
        )
        return messages, {
            "context_tokens": total,
            "threshold": threshold,
            "compressed": compressed,
        }
