"""Skill catalog — progressive disclosure 第 1 级（name + description + path）.

扫描 data/skills/installed（及可选用户/项目目录），供全体智能体共享。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from project_runtime.skills.fs_store import installed_root

logger = logging.getLogger(__name__)

# 目录注入默认预算（字符）；超出先缩短 description
DEFAULT_CATALOG_CHAR_BUDGET = 8000


@dataclass
class SkillCatalogEntry:
    name: str
    description: str
    path: Path
    skill_dir: Path
    source: str  # installed | user_agents | project_agents
    preferred: bool = False


def _read_name_description(skill_md: Path) -> tuple[str, str]:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    name = skill_md.parent.name
    description = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                meta = yaml.safe_load(text[3:end])
            except yaml.YAMLError:
                meta = None
            if isinstance(meta, dict):
                name = str(meta.get("name") or name).strip() or name
                description = str(meta.get("description") or "").strip()
    if not description:
        description = f"Skill {name}"
    return name, description


def _scan_root(root: Path, source: str) -> list[SkillCatalogEntry]:
    if not root.is_dir():
        return []
    out: list[SkillCatalogEntry] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        try:
            name, desc = _read_name_description(md)
        except OSError as e:
            logger.debug("skip skill dir %s: %s", child, e)
            continue
        out.append(
            SkillCatalogEntry(
                name=name,
                description=desc,
                path=md.resolve(),
                skill_dir=child.resolve(),
                source=source,
            )
        )
    return out


def list_catalog(
    *,
    project_root: Path | None = None,
    preferred_names: set[str] | None = None,
    include_user_agents: bool = True,
) -> list[SkillCatalogEntry]:
    """按优先级合并：installed > project .agents/skills > ~/.agents/skills。"""
    preferred_names = preferred_names or set()
    buckets: list[tuple[str, list[SkillCatalogEntry]]] = [
        ("installed", _scan_root(installed_root(), "installed")),
    ]
    if project_root is not None:
        buckets.append((
            "project_agents",
            _scan_root(Path(project_root) / ".agents" / "skills", "project_agents"),
        ))
    if include_user_agents:
        home = Path.home() / ".agents" / "skills"
        buckets.append(("user_agents", _scan_root(home, "user_agents")))

    by_key: dict[str, SkillCatalogEntry] = {}
    # 先扫低优先级，再被高优先级覆盖 → 倒序遍历 buckets
    for _src, entries in reversed(buckets):
        for e in entries:
            key = e.name.strip().lower()
            by_key[key] = e

    # 再按 installed 优先顺序输出：重新正序合并
    by_key.clear()
    for _src, entries in buckets:
        for e in entries:
            key = e.name.strip().lower()
            if key not in by_key:
                by_key[key] = e

    out = list(by_key.values())
    for e in out:
        if e.name.strip().lower() in {n.lower() for n in preferred_names}:
            e.preferred = True
    out.sort(key=lambda x: (not x.preferred, x.name.lower()))
    return out


def format_available_skills_xml(
    entries: list[SkillCatalogEntry],
    *,
    char_budget: int = DEFAULT_CATALOG_CHAR_BUDGET,
) -> str:
    """生成注入提示的 <available_skills> 块。"""
    if not entries:
        return (
            "<available_skills>\n"
            "  <!-- 当前无已安装 Skill；可通过设置 → Skills 导入 -->\n"
            "</available_skills>"
        )

    lines = [
        "<available_skills>",
        "  <!-- 全体智能体共用目录；需要时用 skill_view 或读取 path 打开全文 -->",
    ]
    used = sum(len(x) for x in lines)
    omitted = 0
    for e in entries:
        desc = e.description.replace("\n", " ").strip()
        # 单条 description 上限
        max_desc = 240
        while True:
            pref = " preferred=\"true\"" if e.preferred else ""
            piece = (
                f'  <skill name="{_xml_esc(e.name)}" '
                f'description="{_xml_esc(desc[:max_desc])}" '
                f'path="{_xml_esc(str(e.path))}" '
                f'source="{_xml_esc(e.source)}"{pref}/>'
            )
            if used + len(piece) + 20 <= char_budget:
                lines.append(piece)
                used += len(piece)
                break
            if max_desc > 60:
                max_desc = max(60, max_desc // 2)
                continue
            omitted += 1
            break
    if omitted:
        lines.append(
            f"  <!-- 另有 {omitted} 个 Skill 因目录预算未列出；请调用 skills_list -->"
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _xml_esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def get_skill_dir(name: str) -> Path | None:
    key = name.strip().lower()
    for e in list_catalog(include_user_agents=True):
        if e.name.strip().lower() == key:
            return e.skill_dir
    # 直接试 installed/{name}
    cand = installed_root() / key
    if (cand / "SKILL.md").is_file():
        return cand.resolve()
    return None


def iter_skill_roots() -> list[Path]:
    """所有已安装 Skill 根目录（含用户级）。"""
    roots: list[Path] = []
    seen: set[str] = set()
    for e in list_catalog(include_user_agents=True):
        key = str(e.skill_dir)
        if key not in seen:
            seen.add(key)
            roots.append(e.skill_dir)
    # 兜底：installed 下所有含 SKILL.md 的目录
    for p in installed_root().iterdir() if installed_root().is_dir() else []:
        if p.is_dir() and (p / "SKILL.md").is_file():
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                roots.append(p.resolve())
    return roots


def find_skill_root_for_path(path: Path) -> Path | None:
    resolved = path.resolve()
    for root in iter_skill_roots():
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None
