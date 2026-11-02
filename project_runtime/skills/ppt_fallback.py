"""返修V3 批C: PPT 双层兜底——ppt-master 优先，不可用退 office_pptx。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_ppt_backend(
    skill_ctx: Any | None = None,
    *,
    ppt_master_health: bool | None = None,
) -> str:
    """返回 'ppt-master' | 'office_pptx'。

    ppt_master_health: 调用方探测结果；None 时若 skill 绑定含 ppt-master 则尝试视为可用。
    """
    skill_id = ""
    if skill_ctx is not None:
        skill_id = (
            getattr(skill_ctx, "skill_id", None)
            or (skill_ctx.get("skill_id") if isinstance(skill_ctx, dict) else "")
            or ""
        )
    wants_master = "ppt-master" in str(skill_id).lower()
    if not wants_master and skill_ctx is None and ppt_master_health is None:
        return "office_pptx"
    if ppt_master_health is False:
        logger.info("ppt-master unavailable — fallback office_pptx")
        return "office_pptx"
    if wants_master and ppt_master_health is not False:
        return "ppt-master"
    if ppt_master_health is True:
        return "ppt-master"
    return "office_pptx"
