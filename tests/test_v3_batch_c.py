"""返修单 V3 批C: 四闸 / EXECUTION 导入 / 白名单 / PPT 兜底。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.skills.importer import SkillImportError, SkillPackageImporter


@pytest.fixture
async def dbsession(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3c.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(eng) as s:
        yield s
    await eng.dispose()


def _write_text_skill(root: Path, skill_id: str = "demo-text") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nskill_id: {skill_id}\nname: Demo\nversion: 1.0.0\n"
        "skill_type: PROJECT_METHOD\ndescription: text only\n---\n# Body\n",
        encoding="utf-8",
    )
    return root


def _write_exec_skill(
    root: Path, skill_id: str = "demo-exec", *, evil: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nskill_id: {skill_id}\nname: ExecDemo\nversion: 1.0.0\n"
        "skill_type: TASK_EXECUTION\ndescription: with script\n"
        "allowed-tools: [shell]\n---\n# Body\n",
        encoding="utf-8",
    )
    code = "print('hello')\n" if not evil else "eval('1+1')\n"
    (root / "run.py").write_text(code, encoding="utf-8")
    return root


class TestExecutionImporter:
    @pytest.mark.asyncio
    async def test_default_path_still_rejects_py(self, dbsession, tmp_path):
        """默认文本路径一个字节不放水。"""
        root = _write_exec_skill(tmp_path / "rej")
        with pytest.raises(SkillImportError, match="Executable"):
            await SkillPackageImporter(dbsession).import_directory(str(root))

    @pytest.mark.asyncio
    async def test_execution_tier_allows_py(self, dbsession, tmp_path):
        """SYS-094: allow_executable 放行 .py。"""
        root = _write_exec_skill(tmp_path / "ok", "exec-ok")
        skill = await SkillPackageImporter(dbsession).import_directory(
            str(root), allow_executable=True,
        )
        assert skill.skill_id == "exec-ok"

    @pytest.mark.asyncio
    async def test_text_path_regression(self, dbsession, tmp_path):
        root = _write_text_skill(tmp_path / "txt")
        skill = await SkillPackageImporter(dbsession).import_directory(str(root))
        assert skill.skill_id == "demo-text"


class TestFourGates:
    @pytest.mark.asyncio
    async def test_gate1_rejects_eval(self, tmp_path, monkeypatch):
        from project_runtime.skills import install_gates as ig

        monkeypatch.chdir(tmp_path)
        root = _write_exec_skill(tmp_path / "evil", "evil", evil=True)
        g1 = ig.gate1_static(root, allowed_tools=["shell"])
        assert g1.status == "FAIL"

    @pytest.mark.asyncio
    async def test_gate2_sandbox_skipped_without_bwrap(
        self, tmp_path, monkeypatch,
    ):
        from project_runtime.skills import install_gates as ig
        from project_runtime.skills.sandbox import SkillSandboxBox

        monkeypatch.setattr(SkillSandboxBox, "bwrap_available", staticmethod(lambda: False))
        root = _write_exec_skill(tmp_path / "sbx", "sbx")
        g2 = await ig.gate2_sandbox(root)
        assert g2.status == "SKIP"
        assert g2.mark == "SANDBOX_SKIPPED"

    def test_gate3_trust_threshold(self):
        from project_runtime.skills.install_gates import gate3_trust

        recent = datetime.now(timezone.utc) - timedelta(days=10)
        assert gate3_trust(
            stars=2000, last_push=recent, fetch=False,
        ).status == "PASS"
        assert gate3_trust(
            stars=10, last_push=recent, fetch=False,
        ).status == "FAIL"

    @pytest.mark.asyncio
    async def test_four_gates_green_path(self, tmp_path, monkeypatch):
        """SYS-093: 闸全绿（信任注入、无 bwrap SKIP）→ 落盘+白名单。"""
        from project_runtime.skills import install_gates as ig
        from project_runtime.skills.sandbox import SkillSandboxBox

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(SkillSandboxBox, "bwrap_available", staticmethod(lambda: False))
        root = _write_exec_skill(tmp_path / "pkg", "ppt-like")
        recent = datetime.now(timezone.utc) - timedelta(days=5)
        report = await ig.run_four_gates(
            root,
            skill_id="ppt-like",
            version="1.0.0",
            allowed_tools=["shell"],
            trust_stars=5000,
            trust_last_push=recent,
            skip_trust_fetch=True,
        )
        assert report.ok, report.to_dict()
        assert report.sandbox_mark == "SANDBOX_SKIPPED"
        assert Path(report.asset_dir).exists()
        assert (Path(report.asset_dir) / ".paa_lock.json").exists()
        assert ig.is_whitelisted_skill_script(
            f"python {Path(report.asset_dir) / 'run.py'}"
        )

    @pytest.mark.asyncio
    async def test_auto_install_with_local_path(self, tmp_path, monkeypatch):
        from project_runtime.skills import discovery as disc
        from project_runtime.skills.sandbox import SkillSandboxBox

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            SkillSandboxBox, "bwrap_available", staticmethod(lambda: False),
        )
        root = _write_exec_skill(tmp_path / "cand", "cand1")
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        out = await disc.auto_install_candidates([{
            "name": "cand1",
            "skill_id": "cand1",
            "version": "1.0.0",
            "local_path": str(root),
            "trust_stars": 2000,
            "trust_last_push": recent,
        }])
        assert out["installed"], out


class TestPptFallback:
    def test_fallback_when_master_down(self):
        from project_runtime.skills.ppt_fallback import resolve_ppt_backend

        assert resolve_ppt_backend(
            {"skill_id": "ppt-master"}, ppt_master_health=False,
        ) == "office_pptx"
        assert resolve_ppt_backend(
            {"skill_id": "ppt-master"}, ppt_master_health=True,
        ) == "ppt-master"
        assert resolve_ppt_backend(None) == "office_pptx"


class TestShellSkillWhitelist:
    @pytest.mark.asyncio
    async def test_whitelisted_script_allowed(self, tmp_path, monkeypatch):
        from project_runtime.skills import install_gates as ig
        from project_runtime.skills.sandbox import SkillSandboxBox
        from project_runtime.worker.tools import ShellProvider

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(SkillSandboxBox, "bwrap_available", staticmethod(lambda: False))
        root = _write_exec_skill(tmp_path / "sh", "sh1")
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        report = await ig.run_four_gates(
            root, skill_id="sh1", version="1.0.0",
            allowed_tools=["shell"], trust_stars=2000,
            trust_last_push=recent, skip_trust_fetch=True,
        )
        assert report.ok
        script = str(Path(report.asset_dir) / "run.py")
        prov = ShellProvider()
        # 无 -flag 的白名单脚本应过命令门（执行可能因环境失败，但不应是「不在白名单」）
        r = await prov.invoke({"command": f"python {script}", "workspace": str(tmp_path)})
        assert "不在白名单" not in (r.get("error") or "")
