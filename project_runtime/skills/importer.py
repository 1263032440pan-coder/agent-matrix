"""SkillPackageImporter — safe import of external declarative skill packages (D17B).

Security hard rules (00-global/19 §4):
  - ZIP path traversal rejected
  - Symlink escape rejected
  - File count / package size limits
  - Declarative text files only — executables rejected (INVALID)
  - SKILL.md frontmatter schema validation
  - Validate must PASS before anything enters the usable Registry
"""

from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from project_runtime.skills.enums import SkillSource, SkillStatus, SkillType
from project_runtime.skills.models import SkillDefinition
from project_runtime.skills.registry import SkillRegistry, SkillRegistryError

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".txt"}
EXECUTABLE_EXTENSIONS = {".py", ".sh", ".ps1", ".bat", ".exe", ".dll", ".so", ".com"}
# 返修V3 批C / ADR-039: EXECUTION 层级允许的脚本扩展（仍拒 .exe/.dll）
EXECUTION_ALLOWED_EXTRA = {".py", ".sh"}
# 执行型包常见附属文本（Claude office skills 的 schema / 前端片段等）
EXECUTION_ALLOWED_TEXT_EXTRA = {
    ".xml", ".xsd", ".xsl", ".xslt", ".html", ".htm", ".css",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".svg", ".csv",
}
MAX_FILES = 100
MAX_PACKAGE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_FILES_EXECUTION = 300
MAX_PACKAGE_BYTES_EXECUTION = 100 * 1024 * 1024  # 100MB

REQUIRED_FRONTMATTER = ("skill_id", "name", "version", "skill_type", "description")


class SkillImportError(Exception):
    """Validation failure — package rejected, nothing registered."""


def compute_content_hash(files: dict[str, bytes]) -> str:
    """Stable hash over sorted (relpath, content) pairs."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(files[path])
        h.update(b"\0")
    return h.hexdigest()


def _validate_relpath(relpath: str) -> None:
    p = Path(relpath)
    if p.is_absolute() or ".." in p.parts:
        raise SkillImportError(f"Path traversal rejected: {relpath}")


def _validate_file(
    relpath: str, data: bytes, *, allow_executable: bool = False,
) -> None:
    ext = Path(relpath).suffix.lower()
    if allow_executable:
        # ADR-039 EXECUTION: 允许 .py/.sh + 常见文本附属；仍拒二进制可执行
        hard_ban = EXECUTABLE_EXTENSIONS - EXECUTION_ALLOWED_EXTRA
        if ext in hard_ban:
            raise SkillImportError(f"Executable content forbidden: {relpath}")
        allowed = (
            ALLOWED_EXTENSIONS
            | EXECUTION_ALLOWED_EXTRA
            | EXECUTION_ALLOWED_TEXT_EXTRA
        )
        if ext not in allowed:
            raise SkillImportError(f"File type not allowed: {relpath}")
    else:
        if ext in EXECUTABLE_EXTENSIONS:
            raise SkillImportError(
                f"Executable content forbidden: {relpath}；"
                "含 .py/.sh 请勾选「允许执行型」后再导入"
            )
        if ext not in ALLOWED_EXTENSIONS:
            raise SkillImportError(f"File type not allowed: {relpath}")
    # 字体等二进制在执行型里仍可能出现：仅对文本类做 UTF-8 校验
    text_like = (
        ALLOWED_EXTENSIONS
        | EXECUTION_ALLOWED_EXTRA
        | EXECUTION_ALLOWED_TEXT_EXTRA
    )
    if ext in text_like or not allow_executable:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SkillImportError(f"Non-UTF-8 file: {relpath}") from e


def parse_frontmatter(skill_md: str) -> dict:
    """Parse YAML frontmatter from SKILL.md."""
    if not skill_md.startswith("---"):
        raise SkillImportError("SKILL.md missing YAML frontmatter")
    end = skill_md.find("\n---", 3)
    if end == -1:
        raise SkillImportError("SKILL.md frontmatter not closed")
    try:
        meta = yaml.safe_load(skill_md[3:end])
    except yaml.YAMLError as e:
        raise SkillImportError(f"Frontmatter YAML invalid: {e}") from e
    if not isinstance(meta, dict):
        raise SkillImportError("Frontmatter must be a mapping")
    for field in REQUIRED_FRONTMATTER:
        if field not in meta or meta[field] in (None, ""):
            raise SkillImportError(f"Frontmatter missing required field: {field}")
    try:
        SkillType(meta["skill_type"])
    except ValueError as e:
        raise SkillImportError(f"Unknown skill_type: {meta['skill_type']}") from e
    return meta


class SkillPackageImporter:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._registry = SkillRegistry(session)

    async def import_directory(
        self,
        path: str,
        source: SkillSource = SkillSource.USER,
        *,
        allow_executable: bool = False,
    ) -> SkillDefinition:
        """导入目录。默认文本四防；allow_executable=True 启用 ADR-039 EXECUTION。"""
        root = Path(path)
        if not root.is_dir():
            raise SkillImportError(f"Not a directory: {path}")
        max_files = MAX_FILES_EXECUTION if allow_executable else MAX_FILES
        max_bytes = (
            MAX_PACKAGE_BYTES_EXECUTION if allow_executable else MAX_PACKAGE_BYTES
        )
        files: dict[str, bytes] = {}
        count = 0
        total = 0
        for f in sorted(root.rglob("*")):
            if f.is_symlink():
                raise SkillImportError(f"Symlink escape rejected: {f}")
            if not f.is_file():
                continue
            count += 1
            if count > max_files:
                hint = (
                    f"；当前已扫到超过 {max_files} 个文件。"
                    "默认文本 Skill ≤100；含脚本/大包请勾选「允许执行型」"
                    f"（上限 {MAX_FILES_EXECUTION}）"
                )
                raise SkillImportError(
                    f"Package exceeds {max_files} files{hint}"
                )
            data = f.read_bytes()
            total += len(data)
            if total > max_bytes:
                raise SkillImportError("Package exceeds size limit")
            rel = f.relative_to(root).as_posix()
            _validate_relpath(rel)
            _validate_file(rel, data, allow_executable=allow_executable)
            files[rel] = data
        return await self._register(files, package_ref=str(root), source=source)

    async def import_zip(
        self,
        path: str,
        source: SkillSource = SkillSource.USER,
        *,
        allow_executable: bool = False,
    ) -> SkillDefinition:
        max_files = MAX_FILES_EXECUTION if allow_executable else MAX_FILES
        max_bytes = (
            MAX_PACKAGE_BYTES_EXECUTION if allow_executable else MAX_PACKAGE_BYTES
        )
        files: dict[str, bytes] = {}
        total = 0
        try:
            zf_ctx = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            raise SkillImportError(
                f"不是有效的 ZIP 文件（无法解包）: {e}"
            ) from e
        with zf_ctx as zf:
            infos = zf.infolist()
            # 只计文件条目（目录不占额度；此前把 dir 也算进 100 会误杀）
            file_infos = [i for i in infos if not i.is_dir()]
            if len(file_infos) > max_files:
                hint = (
                    f"；当前 {len(file_infos)} 个文件。"
                    "默认文本 Skill ≤100；含脚本/大包请勾选「允许执行型」"
                    f"（上限 {MAX_FILES_EXECUTION}）"
                )
                raise SkillImportError(
                    f"Package exceeds {max_files} files{hint}"
                )
            for info in file_infos:
                # Symlink check: unix mode high bits
                if (info.external_attr >> 16) & 0o120000 == 0o120000:
                    raise SkillImportError(f"Symlink entry rejected: {info.filename}")
                rel = info.filename
                _validate_relpath(rel)
                data = zf.read(info)
                total += len(data)
                if total > max_bytes:
                    raise SkillImportError("Package exceeds size limit")
                _validate_file(rel, data, allow_executable=allow_executable)
                files[rel] = data
        return await self._register(files, package_ref=str(path), source=source)

    async def _register(
        self, files: dict[str, bytes], package_ref: str, source: SkillSource
    ) -> SkillDefinition:
        if "SKILL.md" not in files:
            raise SkillImportError("Package missing SKILL.md")
        meta = parse_frontmatter(files["SKILL.md"].decode("utf-8"))

        skill_id = str(meta["skill_id"])
        version = str(meta["version"])

        # B09: duplicate (skill_id, version) is an explicit conflict
        if await self._registry.get(skill_id, version) is not None:
            raise SkillRegistryError(
                f"Skill already registered: {skill_id}@{version}"
            )

        content_hash = compute_content_hash(files)

        def _json_list(key: str) -> str | None:
            import json

            v = meta.get(key)
            return json.dumps(v, ensure_ascii=False) if v else None

        skill = SkillDefinition(
            skill_id=skill_id,
            name=str(meta["name"]),
            version=version,
            skill_type=SkillType(meta["skill_type"]),
            description=str(meta["description"]),
            when_to_use=meta.get("when_to_use"),
            domain=_json_list("domain"),
            stages=_json_list("stages"),
            task_types=_json_list("task_types"),
            required_inputs=_json_list("required_inputs"),
            expected_outputs=_json_list("expected_outputs"),
            required_capabilities=_json_list("required_capabilities"),
            compatible_worker_types=_json_list("compatible_worker_types"),
            dependencies=_json_list("dependencies"),
            conflicts=_json_list("conflicts"),
            entrypoint="SKILL.md",
            package_ref=package_ref,
            content_hash=content_hash,
            source=source,  # Runtime-assigned, never from package
            status=SkillStatus.INSTALLED,  # validation passed by this point
        )
        await self._registry.register(skill)
        logger.info("Skill imported: %s@%s hash=%s", skill_id, version, content_hash[:12])
        return skill
