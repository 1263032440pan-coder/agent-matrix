"""Filesystem Skill store — agentskills.io 对齐的安装根（F1）.

权威位置: data/skills/installed/{skill_name}/
安装 = 解压/拷贝到该目录；运行时以目录为准（目录扫描见 catalog，F2）。
"""

from __future__ import annotations

import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from project_runtime.skills.importer import (
    ALLOWED_EXTENSIONS,
    EXECUTABLE_EXTENSIONS,
    EXECUTION_ALLOWED_EXTRA,
    EXECUTION_ALLOWED_TEXT_EXTRA,
    MAX_FILES,
    MAX_FILES_EXECUTION,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_BYTES_EXECUTION,
    SkillImportError,
    compute_content_hash,
)

logger = logging.getLogger(__name__)

INSTALLED_ROOT = Path("data/skills/installed")

# 执行型包常见二进制资源（字体/图片）——不入 UTF-8 校验
EXECUTION_ALLOWED_BINARY_EXTRA = {
    ".ttf", ".otf", ".woff", ".woff2",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
}

_NAME_SAFE = re.compile(r"[^a-z0-9-]+")


@dataclass
class FsInstallResult:
    name: str
    skill_id: str
    version: str
    skill_type: str
    description: str
    install_dir: str
    content_hash: str
    adapted_fields: list[str] = field(default_factory=list)
    has_scripts: bool = False
    file_count: int = 0


def installed_root() -> Path:
    INSTALLED_ROOT.mkdir(parents=True, exist_ok=True)
    return INSTALLED_ROOT


def normalize_skill_name(raw: str) -> str:
    s = (raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    s = _NAME_SAFE.sub("-", s).strip("-")
    if not s:
        raise SkillImportError("Cannot derive skill name from frontmatter")
    return s[:128]


def adapt_frontmatter(meta: dict, *, content_hash: str = "") -> tuple[dict, list[str]]:
    """agentskills/Claude 最小字段 → 本系统可用字段。返回 (meta, adapted_fields)。"""
    adapted: list[str] = []
    out = dict(meta)

    name = str(out.get("name") or "").strip()
    if not name:
        raise SkillImportError("Frontmatter missing required field: name")

    if not out.get("description"):
        out["description"] = name
        adapted.append("description")

    if not out.get("skill_id"):
        out["skill_id"] = normalize_skill_name(name)
        adapted.append("skill_id")
    else:
        out["skill_id"] = normalize_skill_name(str(out["skill_id"]))

    if not out.get("version"):
        suffix = (content_hash or "00000000")[:8]
        out["version"] = f"0.0.0+fs.{suffix}"
        adapted.append("version")

    if not out.get("skill_type"):
        out["skill_type"] = "TASK_EXECUTION"
        adapted.append("skill_type")

    if not out.get("when_to_use"):
        out["when_to_use"] = str(out["description"])
        adapted.append("when_to_use")

    # 校验 skill_type 合法性
    from project_runtime.skills.enums import SkillType

    try:
        SkillType(str(out["skill_type"]))
    except ValueError as e:
        raise SkillImportError(f"Unknown skill_type: {out['skill_type']}") from e

    return out, adapted


def _validate_file_bytes(
    relpath: str, data: bytes, *, allow_executable: bool,
) -> None:
    from project_runtime.skills.importer import _validate_relpath

    _validate_relpath(relpath)
    ext = Path(relpath).suffix.lower()
    if allow_executable and ext in EXECUTION_ALLOWED_BINARY_EXTRA:
        # 二进制资源：只做路径与体积由上层控制
        if ext in (EXECUTABLE_EXTENSIONS - EXECUTION_ALLOWED_EXTRA):
            raise SkillImportError(f"Executable content forbidden: {relpath}")
        return
    # 复用 importer 文本/脚本规则；临时扩展 allowed
    if allow_executable:
        hard_ban = EXECUTABLE_EXTENSIONS - EXECUTION_ALLOWED_EXTRA
        if ext in hard_ban:
            raise SkillImportError(f"Executable content forbidden: {relpath}")
        allowed = (
            ALLOWED_EXTENSIONS
            | EXECUTION_ALLOWED_EXTRA
            | EXECUTION_ALLOWED_TEXT_EXTRA
            | EXECUTION_ALLOWED_BINARY_EXTRA
        )
        if ext not in allowed and ext != "":
            # 无扩展名当文本试 UTF-8
            if ext:
                raise SkillImportError(f"File type not allowed: {relpath}")
        if ext in (
            ALLOWED_EXTENSIONS
            | EXECUTION_ALLOWED_EXTRA
            | EXECUTION_ALLOWED_TEXT_EXTRA
        ) or not ext:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise SkillImportError(f"Non-UTF-8 file: {relpath}") from e
        return

    from project_runtime.skills.importer import _validate_file

    _validate_file(relpath, data, allow_executable=False)


def _read_skill_md_meta(skill_md: bytes, *, content_hash: str = "") -> tuple[dict, list[str]]:
    raw = skill_md.decode("utf-8")
    # 宽松解析：先抽 YAML，再 adapt（不强制旧 REQUIRED 全齐）
    if not raw.startswith("---"):
        raise SkillImportError("SKILL.md missing YAML frontmatter")
    end = raw.find("\n---", 3)
    if end == -1:
        raise SkillImportError("SKILL.md frontmatter not closed")
    import yaml

    try:
        meta = yaml.safe_load(raw[3:end])
    except yaml.YAMLError as e:
        raise SkillImportError(f"Frontmatter YAML invalid: {e}") from e
    if not isinstance(meta, dict):
        raise SkillImportError("Frontmatter must be a mapping")
    return adapt_frontmatter(meta, content_hash=content_hash)


def _group_zip_skills(files: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """按 SKILL.md 所在目录拆成多个 skill 文件集（相对该 skill 根）。"""
    skill_mds = [p for p in files if p.replace("\\", "/").endswith("SKILL.md")]
    if not skill_mds:
        raise SkillImportError("Package missing SKILL.md")

    # 根级 SKILL.md
    if "SKILL.md" in files:
        return {"__root__": files}

    groups: dict[str, dict[str, bytes]] = {}
    for md_path in skill_mds:
        norm = md_path.replace("\\", "/")
        parent = norm[: -len("SKILL.md")].rstrip("/")
        # 跳过明显的模板/合集说明（仍允许 template/ 若用户需要可装）
        bucket: dict[str, bytes] = {}
        prefix = f"{parent}/" if parent else ""
        for path, data in files.items():
            pn = path.replace("\\", "/")
            if parent == "":
                bucket[pn] = data
            elif pn.startswith(prefix):
                bucket[pn[len(prefix):]] = data
        if "SKILL.md" in bucket:
            groups[parent or "__root__"] = bucket

    if not groups:
        raise SkillImportError("Package missing SKILL.md")
    return groups


def _collect_dir_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for f in sorted(root.rglob("*")):
        if f.is_symlink():
            raise SkillImportError(f"Symlink escape rejected: {f}")
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        files[rel] = f.read_bytes()
    return files


def _collect_zip_files(zip_path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        zf_ctx = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise SkillImportError(f"不是有效的 ZIP 文件（无法解包）: {e}") from e
    with zf_ctx as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o120000 == 0o120000:
                raise SkillImportError(f"Symlink entry rejected: {info.filename}")
            rel = info.filename.replace("\\", "/")
            if rel.endswith("/"):
                continue
            files[rel] = zf.read(info)
    return files


def _has_scripts(files: dict[str, bytes]) -> bool:
    return any(
        Path(p).suffix.lower() in {".py", ".sh"} for p in files
    )


def _enforce_limits(
    files: dict[str, bytes], *, allow_executable: bool,
) -> None:
    max_files = MAX_FILES_EXECUTION if allow_executable else MAX_FILES
    max_bytes = (
        MAX_PACKAGE_BYTES_EXECUTION if allow_executable else MAX_PACKAGE_BYTES
    )
    if len(files) > max_files:
        raise SkillImportError(
            f"Package exceeds {max_files} files；当前 {len(files)} 个文件。"
            "默认文本 Skill ≤100；含脚本/大包请勾选「允许执行型」"
            f"（上限 {MAX_FILES_EXECUTION}）"
        )
    total = sum(len(v) for v in files.values())
    if total > max_bytes:
        raise SkillImportError("Package exceeds size limit")
    need_exec = _has_scripts(files)
    if need_exec and not allow_executable:
        sample = next(
            p for p in files if Path(p).suffix.lower() in {".py", ".sh"}
        )
        raise SkillImportError(
            f"Executable content forbidden: {sample}；"
            "含 .py/.sh 请勾选「允许执行型」后再导入"
        )
    for rel, data in files.items():
        _validate_file_bytes(rel, data, allow_executable=allow_executable)


def _write_install_dir(name: str, files: dict[str, bytes], *, overwrite: bool) -> Path:
    dest = installed_root() / name
    if dest.exists():
        if not overwrite:
            raise SkillImportError(
                f"Skill already installed at {dest}；请先删除或允许覆盖"
            )
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest


def install_skill_files(
    files: dict[str, bytes],
    *,
    allow_executable: bool = False,
    overwrite: bool = True,
) -> FsInstallResult:
    """将已归一化到 skill 根的文件集安装到 installed/{name}/。"""
    if "SKILL.md" not in files:
        raise SkillImportError("Package missing SKILL.md")
    _enforce_limits(files, allow_executable=allow_executable)
    content_hash = compute_content_hash(files)
    meta, adapted = _read_skill_md_meta(
        files["SKILL.md"], content_hash=content_hash,
    )
    name = normalize_skill_name(str(meta["name"]))
    # skill_id 与目录名对齐（用 name）
    dir_name = name
    dest = _write_install_dir(dir_name, files, overwrite=overwrite)
    result = FsInstallResult(
        name=str(meta["name"]),
        skill_id=str(meta["skill_id"]),
        version=str(meta["version"]),
        skill_type=str(meta["skill_type"]),
        description=str(meta["description"]),
        install_dir=str(dest.resolve()),
        content_hash=content_hash,
        adapted_fields=adapted,
        has_scripts=_has_scripts(files),
        file_count=len(files),
    )
    logger.info(
        "Skill FS installed: %s -> %s (scripts=%s adapted=%s)",
        result.skill_id, result.install_dir, result.has_scripts, adapted,
    )
    return result


def install_from_zip(
    zip_path: str | Path,
    *,
    allow_executable: bool = False,
    overwrite: bool = True,
) -> list[FsInstallResult]:
    """安装 ZIP：单 Skill 或合集（多个 SKILL.md）→ 多个 installed/ 目录。"""
    path = Path(zip_path)
    all_files = _collect_zip_files(path)
    groups = _group_zip_skills(all_files)
    results: list[FsInstallResult] = []
    errors: list[str] = []
    for key, files in groups.items():
        try:
            # 合集内单包若含脚本，整体 allow_executable 决定是否放行
            results.append(
                install_skill_files(
                    files,
                    allow_executable=allow_executable,
                    overwrite=overwrite,
                )
            )
        except SkillImportError as e:
            errors.append(f"{key}: {e}")
    if not results:
        raise SkillImportError(
            "没有成功安装任何 Skill：" + "; ".join(errors[:5])
        )
    if errors:
        logger.warning("Partial skill install failures: %s", errors[:10])
    return results


def install_from_directory(
    dir_path: str | Path,
    *,
    allow_executable: bool = False,
    overwrite: bool = True,
) -> list[FsInstallResult]:
    root = Path(dir_path)
    if not root.is_dir():
        raise SkillImportError(f"Not a directory: {dir_path}")
    # 目录本身是 skill
    if (root / "SKILL.md").is_file():
        files = _collect_dir_files(root)
        return [
            install_skill_files(
                files, allow_executable=allow_executable, overwrite=overwrite,
            )
        ]
    # 子目录各含 SKILL.md（合集展开）
    results: list[FsInstallResult] = []
    errors: list[str] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            try:
                results.append(
                    install_skill_files(
                        _collect_dir_files(child),
                        allow_executable=allow_executable,
                        overwrite=overwrite,
                    )
                )
            except SkillImportError as e:
                errors.append(f"{child.name}: {e}")
    if not results:
        raise SkillImportError(
            "目录中未找到 SKILL.md：" + ("; ".join(errors[:5]) or str(root))
        )
    return results


def uninstall_skill(name: str) -> bool:
    dest = installed_root() / normalize_skill_name(name)
    if not dest.is_dir():
        return False
    shutil.rmtree(dest, ignore_errors=True)
    return True


def list_installed_dirs() -> list[Path]:
    root = installed_root()
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and (p / "SKILL.md").is_file()
    )
