"""返修单 V3 批B: skill 发现闭环（SKILL_GAP / DISCOVERY / 安装钩子 / 方法论）。

与 REQUEST_RESEARCH 同构：缺口可观测 → 研究类型执行 → 结果路由。
批C 四闸未就绪时 auto_install_candidates 默认全拒，回落 REQUEST_USER_INPUT。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

KIND_SKILL_DISCOVERY = "SKILL_DISCOVERY"
KIND_METHODOLOGY = "METHODOLOGY_RESEARCH"

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---", re.S | re.M,
)


def parse_skill_md_metadata(text: str) -> dict[str, str]:
    """从 SKILL.md（或搜索摘录）抽出 agentskills.io 对齐元数据。

    只收 name/description/when_to_use/license——不含执行物。
    """
    meta: dict[str, str] = {}
    body = text or ""
    m = _FRONTMATTER_RE.search(body)
    block = m.group(1) if m else body
    for key in ("name", "description", "when_to_use", "license"):
        km = re.search(
            rf"(?:^|\n)\s*{key}\s*:\s*[\"']?(.+?)[\"']?\s*(?:\n|$)",
            block,
            re.I,
        )
        if km:
            meta[key] = km.group(1).strip()
    return meta


async def project_has_private_domain(
    session: AsyncSession, project_id: int,
) -> bool:
    """PRIVATE 缺口存在 → 禁止 SKILL_DISCOVERY 外发。"""
    from project_runtime.project_state.enums import GapType
    from project_runtime.project_state.models import (
        MethodGap,
        ProjectReadinessAssessment,
    )

    stmt = (
        select(MethodGap.id)
        .join(
            ProjectReadinessAssessment,
            MethodGap.assessment_id == ProjectReadinessAssessment.id,
        )
        .where(
            ProjectReadinessAssessment.project_id == project_id,
            MethodGap.gap_type == GapType.PRIVATE,
            MethodGap.status == "OPEN",
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def emit_skill_gap(
    session: AsyncSession,
    project_id: int,
    *,
    domain_summary: str,
    skill_type: str = "PROJECT_METHOD",
) -> dict[str, Any]:
    """B1: selector 全池不匹配 → SKILL_GAP 事件 +（非 PRIVATE）建研究。

    返回与 REQUEST_RESEARCH 同构的 pending_request 字典。
    """
    from project_runtime.event.models import RuntimeEvent, RuntimeEventType
    from project_runtime.research.models import ResearchRecord

    summary = (domain_summary or "")[:2000]
    private = await project_has_private_domain(session, project_id)

    session.add(RuntimeEvent(
        event_type=RuntimeEventType.SKILL_GAP.value,
        project_id=project_id,
        phase="SKILL_RESOLUTION",
        message=f"skill miss: no {skill_type} matched",
        actor="SYSTEM",
        payload=json.dumps({
            "skill_type": skill_type,
            "domain_summary": summary[:500],
            "network_blocked": private,
        }, ensure_ascii=False),
    ))

    req: dict[str, Any] = {
        "request_type": "SKILL_GAP",
        "skill_type": skill_type,
        "domain_summary": summary[:500],
        "reason": "no matching skill in pool",
        "network_blocked": private,
        "research_id": None,
    }
    if private:
        # 事件留痕，但不建外发研究
        await session.flush()
        return req

    question = (
        f"[project:{project_id}] {skill_type} skill for: {summary[:200]}"
    )
    # 幂等: 同 question+kind 复用
    existing = (
        await session.execute(
            select(ResearchRecord).where(
                ResearchRecord.question == question,
                ResearchRecord.research_kind == KIND_SKILL_DISCOVERY,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        req["research_id"] = existing.id
        await session.flush()
        return req

    rec = ResearchRecord(
        project_id=project_id,
        research_kind=KIND_SKILL_DISCOVERY,
        question=question,
        status="OPEN",
        findings_json=json.dumps({
            "skill_type": skill_type,
            "domain_summary": summary[:500],
        }, ensure_ascii=False),
    )
    session.add(rec)
    await session.flush()
    req["research_id"] = rec.id
    return req


async def run_skill_discovery(
    session: AsyncSession,
    project_id: int,
    *,
    question: str,
) -> list[dict[str, str]]:
    """B2: 联网搜 SKILL.md 元数据候选（PRIVATE 已在 emit 层挡住建单）。"""
    if await project_has_private_domain(session, project_id):
        return []

    from project_runtime.research.executor import WebResearchExecutor

    q = question.strip()
    if "SKILL.md" not in q:
        q = f"{q} SKILL.md agentskills"
    exe = WebResearchExecutor(session)
    hits = await exe._search(q)  # noqa: SLF001 — 复用既有 RESEARCH provider 链
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for h in hits or []:
        blob = "\n".join([
            str(h.get("title") or ""),
            str(h.get("content") or ""),
            str(h.get("source") or ""),
        ])
        meta = parse_skill_md_metadata(blob)
        if not meta.get("name"):
            # 弱命中: 用 title 充当 name（仍要求像 skill 页）
            src = str(h.get("source") or "")
            if "SKILL.md" not in src and "skill" not in src.lower():
                continue
            meta["name"] = (h.get("title") or "unknown-skill")[:80]
            meta.setdefault("description", (h.get("content") or "")[:300])
        name = meta["name"]
        if name in seen:
            continue
        seen.add(name)
        candidates.append({
            "name": name,
            "description": meta.get("description", ""),
            "when_to_use": meta.get("when_to_use", ""),
            "license": meta.get("license", ""),
            "source_url": str(h.get("source") or "")[:500],
        })
    return candidates


async def auto_install_candidates(
    candidates: list[dict],
    session: AsyncSession | None = None,
) -> dict[str, list]:
    """B3→批C: 候选进四闸；需 local_path 才可装（仅元数据 URL → 拒）。

    提供 session 时：四闸通过后以 EXECUTION 层级 import_directory 入库。
    """
    from pathlib import Path

    from project_runtime.event.models import RuntimeEvent, RuntimeEventType
    from project_runtime.skills.enums import SkillSource
    from project_runtime.skills.importer import (
        SkillImportError,
        SkillPackageImporter,
    )
    from project_runtime.skills.install_gates import run_four_gates
    from project_runtime.skills.registry import SkillRegistryError

    installed: list = []
    rejected: list = []
    for c in candidates:
        local = c.get("local_path")
        if not local:
            rejected.append({**c, "reason": "no local_path for four-gate install"})
            continue

        root = Path(str(local))
        if not root.is_dir():
            rejected.append({**c, "reason": "local_path not a directory"})
            continue
        skill_id = str(c.get("skill_id") or c.get("name") or "unknown")
        version = str(c.get("version") or "0.0.0")
        report = await run_four_gates(
            root,
            skill_id=skill_id,
            version=version,
            github_repo=c.get("github_repo"),
            trust_stars=c.get("trust_stars"),
            trust_last_push=c.get("trust_last_push"),
            source_ref=str(c.get("source_url") or ""),
            skip_trust_fetch=(
                c.get("trust_stars") is not None or not c.get("github_repo")
            ),
        )
        if session is not None:
            session.add(RuntimeEvent(
                event_type=RuntimeEventType.SKILL_LOADED.value,
                project_id=c.get("project_id"),
                phase="SKILL_INSTALL_GATES",
                message=(
                    f"four-gate {'PASS' if report.ok else 'FAIL'} "
                    f"{skill_id}@{version}"
                ),
                actor="SYSTEM",
                payload=__import__("json").dumps(
                    report.to_dict(), ensure_ascii=False,
                ),
            ))
        if not report.ok:
            rejected.append({**c, "reason": "four-gate failed",
                             "gates": report.to_dict()})
            continue
        entry = {
            **c,
            "asset_dir": report.asset_dir,
            "sandbox_mark": report.sandbox_mark,
            "whitelist_entries": report.whitelist_entries,
            "allow_executable": True,
            "gates": report.to_dict(),
        }
        if session is not None:
            try:
                # 优先从落盘资产目录登记（hash 锁定后的副本）
                import_root = report.asset_dir or str(root)
                sk = await SkillPackageImporter(session).import_directory(
                    import_root,
                    source=SkillSource.PLUGIN,
                    allow_executable=True,
                )
                entry["registered_skill_id"] = sk.skill_id
                entry["registered_version"] = sk.version
            except (SkillImportError, SkillRegistryError) as e:
                rejected.append({**c, "reason": f"registry import: {e}",
                                 "gates": report.to_dict()})
                continue
        installed.append(entry)
    return {"installed": installed, "rejected": rejected}


async def route_discovery_candidates(
    session: AsyncSession,
    project_id: int,
    candidates: list[dict],
) -> dict[str, Any]:
    """命中 → 安装闸；全拒/无结果 → REQUEST_USER_INPUT。"""
    if not candidates:
        return {
            "request_type": "REQUEST_USER_INPUT",
            "reason": "SKILL_DISCOVERY 无候选——请提供可安装的 skill 来源"
                      "（目录/ZIP/仓库）",
            "project_id": project_id,
        }
    tagged = [{**c, "project_id": project_id} for c in candidates]
    result = await auto_install_candidates(tagged, session=session)
    if result.get("installed"):
        return {
            "request_type": "SKILL_INSTALLED",
            "installed": result["installed"],
            "project_id": project_id,
        }
    return {
        "request_type": "REQUEST_USER_INPUT",
        "reason": (
            f"候选 skill {len(candidates)} 个均未通过自动安装闸——"
            "请人工确认安装来源或调整信任策略"
        ),
        "rejected": result.get("rejected") or candidates,
        "project_id": project_id,
    }


async def run_gpt_researcher(question: str) -> dict[str, Any]:
    """B4 路线 A: GPT-Researcher 库嵌入；未安装则结构化降级空壳由上层补。"""
    try:
        from gpt_researcher import GPTResearcher  # type: ignore
    except ImportError:
        logger.info("gpt-researcher not installed — methodology fallback")
        return await _methodology_fallback(question)

    try:
        researcher = GPTResearcher(query=question, report_type="research_report")
        await researcher.conduct_research()
        report = await researcher.write_report()
        return await _structure_methodology_report(question, str(report))
    except Exception:  # noqa: BLE001
        logger.debug("GPTResearcher failed", exc_info=True)
        return await _methodology_fallback(question)


async def _methodology_fallback(question: str) -> dict[str, Any]:
    """无 GPT-Researcher 时: 既有搜索 + LLM 抽阶段；再失败给最小种子。"""
    try:
        from project_runtime.extension.shared import shared_registry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.research.providers import register_search_providers

        reg = shared_registry()
        register_search_providers(reg)
        hits: list = []
        for info in reg.list_providers(CapabilityType.RESEARCH):
            if not info.enabled:
                continue
            try:
                hits = await info.provider.invoke({"query": question})
                if hits:
                    break
            except Exception:  # noqa: BLE001
                continue
        if hits:
            return await _structure_methodology_report(
                question,
                "\n".join(
                    f"{h.get('title')}: {h.get('content')}" for h in hits[:6]
                ),
            )
    except Exception:  # noqa: BLE001
        logger.debug("methodology search fallback failed", exc_info=True)

    # 九词默认种子（批A）——无外网也能不阻断规划
    from project_runtime.worker.registry import DEFAULT_STAGE_SEED

    stages = [
        {"name": s, "outputs": [], "dependencies": []}
        for s in DEFAULT_STAGE_SEED
        if s != "misc"
    ]
    return {
        "summary": f"【降级种子】无深度研究结果，使用默认阶段种子（问: {question[:80]}）",
        "stages": stages,
    }


async def _structure_methodology_report(
    question: str, raw: str,
) -> dict[str, Any]:
    """LLM 把长文压成 {summary, stages[{name,outputs,dependencies}]}。"""
    try:
        from project_runtime.llm import (
            load_llm_settings,
            normalize_model_for_gateway,
        )
        from project_runtime.llm.provider import llm_complete

        cfg = load_llm_settings()
        if not (cfg.base_url and cfg.api_key and cfg.model):
            raise RuntimeError("llm not configured")
        model_id = normalize_model_for_gateway(cfg.model, cfg.base_url)
        prompt = (
            "将下列行业方法论材料整理为 JSON（仅输出 JSON）:\n"
            '{"summary":"...","stages":[{"name":"阶段名","outputs":["产出"],'
            '"dependencies":["上游阶段名"]}]}\n'
            f"问题: {question[:200]}\n材料:\n{raw[:6000]}"
        )
        result = await llm_complete(
            model=model_id,
            api_base=cfg.base_url,
            api_key=cfg.api_key,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            timeout=90,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error"))
        text = result["response"].choices[0].message.content or ""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        stages = data.get("stages") or []
        if isinstance(stages, list) and stages:
            return {
                "summary": str(data.get("summary") or "")[:1000],
                "stages": stages,
            }
    except Exception:  # noqa: BLE001
        logger.debug("structure methodology failed", exc_info=True)
    return {
        "summary": raw[:800],
        "stages": [{"name": "方法调研", "outputs": ["调研纪要"],
                    "dependencies": []}],
    }


async def run_methodology_research(
    session: AsyncSession,
    project_id: int,
    *,
    question: str,
) -> dict[str, Any]:
    """B4: 建/答 METHODOLOGY_RESEARCH，结构化报告喂规划器。"""
    from project_runtime.research.models import ResearchRecord

    if await project_has_private_domain(session, project_id):
        return {
            "summary": "PRIVATE 域跳过联网方法论研究",
            "stages": [],
            "skipped_private": True,
        }

    q = f"[project:{project_id}] methodology: {question.strip()[:300]}"
    existing = (
        await session.execute(
            select(ResearchRecord).where(
                ResearchRecord.question == q,
                ResearchRecord.research_kind == KIND_METHODOLOGY,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "ANSWERED":
        return json.loads(existing.findings_json or "{}")

    report = await run_gpt_researcher(question)
    rec = existing or ResearchRecord(
        project_id=project_id,
        research_kind=KIND_METHODOLOGY,
        question=q,
        status="OPEN",
    )
    if existing is None:
        session.add(rec)
        await session.flush()
    rec.summary = str(report.get("summary") or "")[:2000]
    rec.findings_json = json.dumps(report, ensure_ascii=False)
    rec.status = "ANSWERED"
    await session.flush()
    return report


async def execute_skill_discovery_record(
    session: AsyncSession, research_id: int,
) -> dict[str, Any]:
    """执行 OPEN 的 SKILL_DISCOVERY：搜候选 → 安装路由。"""
    from project_runtime.research.models import ResearchRecord

    rec = await session.get(ResearchRecord, research_id)
    if rec is None or rec.research_kind != KIND_SKILL_DISCOVERY:
        return {"ok": False, "error": "not a SKILL_DISCOVERY record"}
    if rec.status == "ANSWERED":
        return json.loads(rec.findings_json or "{}")

    candidates = await run_skill_discovery(
        session, rec.project_id, question=rec.question,
    )
    route = await route_discovery_candidates(
        session, rec.project_id, candidates,
    )
    payload = {"candidates": candidates, "route": route}
    rec.findings_json = json.dumps(payload, ensure_ascii=False)
    rec.summary = (
        f"发现候选 {len(candidates)}；路由 {route.get('request_type')}"
    )
    rec.status = "ANSWERED"
    await session.flush()
    return payload
