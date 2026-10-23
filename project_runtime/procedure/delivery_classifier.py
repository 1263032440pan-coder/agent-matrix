"""V1.6 序1: 交付画像语义判定器（纯事实记录——机制）。

钩子化原则（设计单 V1.6 §一）: 判定结果只决定"注入不注入 skill《交付基线》"，
不直接约束任何产物——验收标准全在 skill 层。

三级判定链:
  1. LLM 语义判定（主通道）: /plan 链 normalize 后调用
  2. 无 LLM（测试态）: 扩词表正则兜底
  3. 双失败 → NOT_SOFTWARE（保守: 不注入, UI 手动重新判定兜底——P2-3）
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SOFTWARE_DEV = "SOFTWARE_DEV"
NOT_SOFTWARE = "NOT_SOFTWARE"

# 兜底正则（无 LLM 时）: 材料出现这些词 → 大概率开发需求
_DEV_KEYWORDS = re.compile(
    r"开发文档|接口文档|系统设计|数据库表|部署|实现|编码|源码|源代码|"
    r"软件需求|开发需求|功能需求|构建|脚手架|API\s*(设计|开发|实现)|"
    r"做个.{0,6}(工具|系统|应用|网站|小程序|APP)"
)


class DeliveryClassifier:
    """语义判定: 材料是不是软件开发需求。"""

    def __init__(self, session: AsyncSession, llm_fn=None):
        self._session = session
        # llm_fn: async (prompt: str) -> str | None —— 注入点
        # None=真 LLM（按需 lazy 加载）; False=禁用（测试态走正则）;
        # callable=stub（测试注入）
        self._llm_fn = llm_fn

    async def classify(self, material_text: str) -> str:
        """判定材料文本 → SOFTWARE_DEV | NOT_SOFTWARE。"""
        text = (material_text or "").strip()
        if not text:
            return NOT_SOFTWARE
        # 主通道: LLM 语义判定
        reason = ""
        try:
            answer = await self._call_llm(text)
            if answer is not None:
                result = self._parse(answer)
                if result is not None:
                    return result
                reason = "LLM 输出不可解析"
        except Exception as e:  # noqa: BLE001 — 判定失败走兜底
            reason = f"LLM 调用失败: {e}"
            logger.debug("delivery classify LLM failed: %s", e)
        # 兜底: 正则
        if _DEV_KEYWORDS.search(text):
            if reason:
                logger.info(
                    "delivery classify: LLM 失败走正则兜底（%s）", reason
                )
            return SOFTWARE_DEV
        # 双失败/无命中 → 保守不注入（P2-3: 反向代价=基线静默不生效,
        # UI 重新判定入口兜底）
        return NOT_SOFTWARE

    async def _call_llm(self, text: str) -> str | None:
        if self._llm_fn is False:
            return None  # 测试态禁用
        if callable(self._llm_fn):
            return await self._llm_fn(text)
        # 真 LLM: 按需加载（绝不在测试里读全局配置——with_llm 纪律）
        from project_runtime.llm import (
            load_llm_settings,
            normalize_model_for_gateway,
        )
        from project_runtime.llm.provider import llm_acomplete

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            return None
        prompt = (
            "判断以下项目材料是否为软件开发需求（要求开发/构建软件系统或工具）。\n"
            "是→输出 {\"software_dev\": true, \"reason\": \"...\"}；"
            "否→输出 {\"software_dev\": false, \"reason\": \"...\"}。\n"
            "注意: 纯需求调研、软件选型评估、操作手册、流程规范类文档不是开发需求。\n"
            "只输出 JSON，不要其他内容。\n\n材料（截取）:\n" + text[:3000]
        )
        # 返修单 V1.6-判定器: llm_acomplete 是 kwargs-only 签名（provider.py:107）
        # ——必须关键字传参（位置参数 TypeError, 主通道曾 100% 失败走兜底）
        result = await llm_acomplete(
            model=normalize_model_for_gateway(cfg.model, cfg.base_url),
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,   # 判定只要一个 JSON——防推理模型思考链拉长
            timeout=60,       # 轻调用——不陪跑网关慢流（extractor 605s 是长文场景）
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "LLM failed"))
        # acompletion 非流式 → ChatCompletion; 取 content, 取不到落兜底
        try:
            return result["response"].choices[0].message.content
        except (AttributeError, IndexError):
            return None

    @staticmethod
    def _parse(answer: str) -> str | None:
        """解析 LLM 输出 → 判定值; 不可解析返回 None。"""
        try:
            m = re.search(r"\{[^}]*\}", answer or "", re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
            return SOFTWARE_DEV if data.get("software_dev") else NOT_SOFTWARE
        except (ValueError, AttributeError, TypeError):
            return None

    async def classify_and_store(self, project_id: int) -> str:
        """判定并落库 Project.delivery_profile。

        返修单-V1.6-判定器锁竞争: 落库必须**立即 commit**（不能只 flush）。
        SQLAlchemy flush 即开写事务持 SQLite 写锁直到 commit——此前
        flush 的锁跨越 /plan 链 _normalize 整个 LLM 抽取流（5-30 分钟），
        THINKING_CHUNK 事件全部 database is locked（思维链不显示的
        根因），并发 API 请求同被锁（create_project 500）。
        判定是独立事实: commit 释放锁 + UI 立即可见（不等规划跑完）。
        调用方 _normalize 已用返回值（内存值）传 extractor——commit
        后 session 里的事务状态由 _stage() 的范式消化, 不影响后续。
        """
        text = await self._project_text(project_id)
        profile = await self.classify(text)
        from sqlalchemy import update

        from project_runtime.project_state.models import Project

        await self._session.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(delivery_profile=profile)
        )
        await self._session.commit()
        logger.info(
            "delivery profile: project %d → %s", project_id, profile,
        )
        return profile

    async def _project_text(self, project_id: int) -> str:
        """项目方法资料全文（与 skill_planning._project_doc_text 同源）。"""
        from sqlalchemy import select

        from project_runtime.project_state.models import MethodAsset

        rows = (
            await self._session.execute(
                select(MethodAsset.content)
                .where(MethodAsset.project_id == project_id)
                .order_by(MethodAsset.id.desc())
                .limit(3)
            )
        ).scalars().all()
        return "\n\n".join(r or "" for r in rows)
