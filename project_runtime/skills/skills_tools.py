"""skills_list / skill_view — agentskills.io 按需加载工具（F3）。"""

from __future__ import annotations

from pathlib import Path

from project_runtime.skills.catalog import get_skill_dir, list_catalog


def _ok(body: dict) -> dict:
    return {"ok": True, **body}


def _err(code: str, message: str, hint: str = "") -> dict:
    out = {"ok": False, "error_code": code, "error": message}
    if hint:
        out["hint"] = hint
    return out


class SkillsListProvider:
    name = "tool:skills_list"
    permission = "READ_ONLY"
    priority = 140
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        type(self).capability = CapabilityType.TOOL

    def tool_card(self) -> dict:
        from project_runtime.worker.tool_card import tool_card_from_legacy
        from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

        return tool_card_from_legacy(self.name, TOOL_SCHEMAS.get(self.name, {}))

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_card import schema_from_tool_card

        return schema_from_tool_card(self.name, self.tool_card())

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        entries = list_catalog(include_user_agents=True)
        skills = [
            {
                "name": e.name,
                "description": e.description,
                "path": str(e.path),
                "skill_dir": str(e.skill_dir),
                "source": e.source,
                "preferred": e.preferred,
            }
            for e in entries
        ]
        # 必须有 stdout：工具循环只把 stdout/content 喂回模型
        lines = [f"已安装 Skill {len(skills)} 个："]
        for s in skills[:40]:
            desc = (s.get("description") or "").replace("\n", " ")[:120]
            lines.append(f"- {s['name']}: {desc}")
        if len(skills) > 40:
            lines.append(f"…另有 {len(skills) - 40} 个，请 skill_view 按名打开")
        return _ok({
            "count": len(skills),
            "skills": skills,
            "stdout": "\n".join(lines),
        })


class SkillViewProvider:
    name = "tool:skill_view"
    permission = "READ_ONLY"
    priority = 140
    enabled = True

    def __init__(self) -> None:
        from project_runtime.plugin.capability import CapabilityType

        type(self).capability = CapabilityType.TOOL

    def tool_card(self) -> dict:
        from project_runtime.worker.tool_card import tool_card_from_legacy
        from project_runtime.worker.tool_schemas import TOOL_SCHEMAS

        return tool_card_from_legacy(self.name, TOOL_SCHEMAS.get(self.name, {}))

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_card import schema_from_tool_card

        return schema_from_tool_card(self.name, self.tool_card())

    def is_usable(self, registry=None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def invoke(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        if not name:
            return _err(
                "MISSING_FIELD",
                "skill_view 缺少 name",
                '示例: skill_view {"name": "xlsx"}',
            )
        skill_dir = get_skill_dir(name)
        if skill_dir is None:
            return _err(
                "NOT_FOUND",
                f"未找到 Skill: {name}",
                "先调用 skills_list 查看已安装名称",
            )
        rel = str(payload.get("path") or "").strip().replace("\\", "/")
        if not rel or rel in (".", "SKILL.md"):
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                return _err(
                    "NOT_FOUND",
                    f"Skill {name} 缺少 SKILL.md",
                    "检查 data/skills/installed 安装是否完整",
                )
            text = md.read_text(encoding="utf-8", errors="replace")
            # 文件树摘要
            files: list[str] = []
            for f in sorted(skill_dir.rglob("*")):
                if f.is_file():
                    files.append(f.relative_to(skill_dir).as_posix())
                if len(files) >= 80:
                    files.append("…")
                    break
            body = text[:20000]
            header = (
                f"# skill_view name={name} path=SKILL.md "
                f"({len(text)} chars"
                f"{', truncated' if len(text) > 20000 else ''})\n"
                f"files: {', '.join(files[:20])}"
                f"{'…' if len(files) > 20 else ''}\n\n"
            )
            return _ok({
                "name": name,
                "skill_dir": str(skill_dir),
                "path": "SKILL.md",
                "content": body,
                "truncated": len(text) > 20000,
                "files": files,
                "stdout": header + body,
            })

        # 相对路径禁闭在 skill_dir 内
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            return _err("OUT_OF_SCOPE", f"非法路径: {rel}")
        target = (skill_dir / rel).resolve()
        try:
            target.relative_to(skill_dir.resolve())
        except ValueError:
            return _err("OUT_OF_SCOPE", f"路径越出 Skill 根: {rel}")
        if not target.is_file():
            return _err(
                "NOT_FOUND",
                f"文件不存在: {rel}",
                "path 为 Skill 根下相对路径，如 SKILL.md、scripts/clean.py",
            )
        raw = target.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            note = "二进制文件，请用 shell/专用工具处理"
            return _ok({
                "name": name,
                "path": rel,
                "binary": True,
                "size": len(raw),
                "content": "",
                "note": note,
                "stdout": f"[binary] {rel} size={len(raw)}; {note}",
            })
        body = content[:20000]
        return _ok({
            "name": name,
            "skill_dir": str(skill_dir),
            "path": rel,
            "content": body,
            "truncated": len(content) > 20000,
            "stdout": f"# skill_view name={name} path={rel}\n\n{body}",
        })


def register_skill_tools(registry) -> None:
    registry.register(SkillsListProvider())
    registry.register(SkillViewProvider())
