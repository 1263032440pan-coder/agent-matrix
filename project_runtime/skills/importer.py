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
MAX_FILES = 100
MAX_PACKAGE_BYTES = 10 * 1024 * 1024  # 10MB

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


def _validate_file(relpath: str, data: bytes) -> None:
    ext = Path(relpath).suffix.lower()
    if ext in EXECUTABLE_EXTENSIONS:
        raise SkillImportError(f"Executable content forbidden: {relpath}")
    if ext not in ALLOWED_EXTENSIONS:
        raise SkillImportError(f"File type not allowed: {relpath}")
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
        self, path: str, source: SkillSource = SkillSource.USER
    ) -> SkillDefinition:
        root = Path(path)
        if not root.is_dir():
            raise SkillImportError(f"Not a directory: {path}")
        files: dict[str, bytes] = {}
        count = 0
        total = 0
        for f in sorted(root.rglob("*")):
            if f.is_symlink():
                raise SkillImportError(f"Symlink escape rejected: {f}")
            if not f.is_file():
                continue
            count += 1
            if count > MAX_FILES:
                raise SkillImportError(f"Package exceeds {MAX_FILES} files")
            data = f.read_bytes()
            total += len(data)
            if total > MAX_PACKAGE_BYTES:
                raise SkillImportError("Package exceeds size limit")
            rel = f.relative_to(root).as_posix()
            _validate_relpath(rel)
            _validate_file(rel, data)
            files[rel] = data
        return await self._register(files, package_ref=str(root), source=source)

    async def import_zip(
        self, path: str, source: SkillSource = SkillSource.USER
    ) -> SkillDefinition:
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
            if len(infos) > MAX_FILES:
                raise SkillImportError(f"Package exceeds {MAX_FILES} files")
            for info in infos:
                # Symlink check: unix mode high bits
                if (info.external_attr >> 16) & 0o120000 == 0o120000:
                    raise SkillImportError(f"Symlink entry rejected: {info.filename}")
                if info.is_dir():
                    continue
                rel = info.filename
                _validate_relpath(rel)
                data = zf.read(info)
                total += len(data)
                if total > MAX_PACKAGE_BYTES:
                    raise SkillImportError("Package exceeds size limit")
                _validate_file(rel, data)
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
