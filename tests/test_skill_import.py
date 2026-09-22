"""D17B Skill Package Import — D17.5 matrix B01-B10."""

import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.models import Project  # noqa: F401 — registers table
from project_runtime.skills.importer import (
    SkillImportError,
    SkillPackageImporter,
    compute_content_hash,
)
from project_runtime.skills.registry import SkillRegistry, SkillRegistryError

VALID_SKILL_MD = """---
skill_id: mock-skill
name: Mock Skill
version: 1.0.0
skill_type: TASK_EXECUTION
description: Test fixture skill
---

# Body
"""


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _make_dir(tmp_path, files: dict[str, str | bytes]) -> str:
    root = tmp_path / "pkg"
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            f.write_bytes(content)
        else:
            f.write_text(content, encoding="utf-8")
    return str(root)


def _make_zip(tmp_path, files: dict[str, str | bytes], name="pkg.zip") -> str:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return str(zp)


# B01 — valid directory imports
@pytest.mark.asyncio
async def test_b01_valid_directory(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD, "references/r.md": "# ref"})
    imp = SkillPackageImporter(dbsession)
    skill = await imp.import_directory(path)
    assert skill.skill_id == "mock-skill"
    assert skill.version == "1.0.0"
    assert skill.status.value == "INSTALLED"
    assert skill.content_hash and len(skill.content_hash) == 64


# B02 — valid ZIP imports
@pytest.mark.asyncio
async def test_b02_valid_zip(dbsession, tmp_path):
    zp = _make_zip(tmp_path, {"SKILL.md": VALID_SKILL_MD})
    imp = SkillPackageImporter(dbsession)
    skill = await imp.import_zip(zp)
    assert skill.skill_id == "mock-skill"


# B03 — missing SKILL.md rejected
@pytest.mark.asyncio
async def test_b03_missing_skill_md(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"README.md": "# nope"})
    with pytest.raises(SkillImportError, match="SKILL.md"):
        await SkillPackageImporter(dbsession).import_directory(path)


# B04 — frontmatter schema error rejected
@pytest.mark.asyncio
async def test_b04_bad_frontmatter(dbsession, tmp_path):
    bad = VALID_SKILL_MD.replace("skill_type: TASK_EXECUTION\n", "")
    path = _make_dir(tmp_path, {"SKILL.md": bad})
    with pytest.raises(SkillImportError, match="skill_type"):
        await SkillPackageImporter(dbsession).import_directory(path)


@pytest.mark.asyncio
async def test_b04_unknown_skill_type(dbsession, tmp_path):
    bad = VALID_SKILL_MD.replace("TASK_EXECUTION", "WIZARDRY")
    path = _make_dir(tmp_path, {"SKILL.md": bad})
    with pytest.raises(SkillImportError, match="skill_type"):
        await SkillPackageImporter(dbsession).import_directory(path)


# B05 — ZIP path traversal rejected
@pytest.mark.asyncio
async def test_b05_zip_path_traversal(dbsession, tmp_path):
    zp = _make_zip(tmp_path, {"SKILL.md": VALID_SKILL_MD, "../evil.md": "x"})
    with pytest.raises(SkillImportError, match="traversal"):
        await SkillPackageImporter(dbsession).import_zip(zp)


# B06 — symlink escape rejected (directory import)
@pytest.mark.asyncio
async def test_b06_symlink_escape(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD})
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    import os

    link = tmp_path / "pkg" / "references" / "link.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(SkillImportError, match="Symlink"):
        await SkillPackageImporter(dbsession).import_directory(path)


# B07 — executable content rejected
@pytest.mark.asyncio
@pytest.mark.parametrize("ext", [".py", ".sh", ".ps1", ".exe", ".dll"])
async def test_b07_executable_rejected(dbsession, tmp_path, ext):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD, f"run{ext}": "x"})
    with pytest.raises(SkillImportError, match="Executable"):
        await SkillPackageImporter(dbsession).import_directory(path)


# B08 — size/file-count policy
@pytest.mark.asyncio
async def test_b08_file_count_limit(dbsession, tmp_path):
    files = {"SKILL.md": VALID_SKILL_MD}
    for i in range(101):
        files[f"references/r{i}.md"] = "x"
    path = _make_dir(tmp_path, files)
    with pytest.raises(SkillImportError, match="files"):
        await SkillPackageImporter(dbsession).import_directory(path)


# B09 — duplicate import conflict
@pytest.mark.asyncio
async def test_b09_duplicate_conflict(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD})
    imp = SkillPackageImporter(dbsession)
    await imp.import_directory(path)
    with pytest.raises(SkillRegistryError, match="already registered"):
        await imp.import_directory(path)
    # Only one usable entry exists
    assert len(await SkillRegistry(dbsession).list()) == 1


# B10 — content hash stable
def test_b10_hash_stable():
    files = {"SKILL.md": b"abc", "references/r.md": b"def"}
    h1 = compute_content_hash(files)
    h2 = compute_content_hash(dict(reversed(list(files.items()))))
    assert h1 == h2
    assert compute_content_hash({"SKILL.md": b"abd"}) != h1


# Disallowed file type (non-executable but not whitelisted)
@pytest.mark.asyncio
async def test_disallowed_extension(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD, "data.csv": "a,b"})
    with pytest.raises(SkillImportError, match="not allowed"):
        await SkillPackageImporter(dbsession).import_directory(path)


# Failed import leaves registry empty (no partial entry)
@pytest.mark.asyncio
async def test_failed_import_registers_nothing(dbsession, tmp_path):
    path = _make_dir(tmp_path, {"SKILL.md": VALID_SKILL_MD, "evil.py": "x"})
    with pytest.raises(SkillImportError):
        await SkillPackageImporter(dbsession).import_directory(path)
    assert await SkillRegistry(dbsession).list() == []
