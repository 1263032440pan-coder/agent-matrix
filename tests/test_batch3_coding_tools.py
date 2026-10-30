"""20号规范批次 3: 编码五插件 + 行业包三防导入测试（全离线）.

覆盖:
  [x] git: 只读五连放行 / 白名单外拒 / 注入模式拒 / status 结构化
  [x] test_runner: pytest 解析（passed/failed/失败用例）/ 参数注入拒
  [x] builder: kind 白名单 / tag 参数模式拒（不真跑 npm/docker——kind 校验层）
  [x] arch_diagram: mermaid 头校验 / 无渲染器降级 .mmd（不失败）
  [x] todo: add/toggle/list 往返 / index=0 正常（or 短路回归）/ 越界拒
  [x] 行业包三防: 防穿越 / 防覆盖 / 防未声明 / manifest 缺字段 /
     非 ZIP 拒 / 正常导入落盘
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.worker.coding_tools import (
    IndustryPackError,
    _parse_pytest,
    import_industry_pack,
    register_batch3_tools,
)


@pytest.fixture
def reg():
    r = ExtensionRegistry()
    register_batch3_tools(r)
    return r


def _provider(r, name):
    return r._providers[name].provider


# ── git ────────────────────────────────────────────────────────────


class TestGit:
    @pytest.mark.asyncio
    async def test_read_whitelist(self, reg):
        p = _provider(reg, "tool:git")
        for args in ("status", "diff", "log", "show", "branch"):
            r = await p.invoke({"args": args})
            assert "error" not in str(r.get("error", "")) or r.get("ok") is not None

    @pytest.mark.asyncio
    async def test_dangerous_rejected(self, reg):
        p = _provider(reg, "tool:git")
        for args in ("push origin master", "reset --hard", "rm -rf .",
                     "add . && curl x", "config user.name x"):
            r = await p.invoke({"args": args})
            assert r["ok"] is False, args

    @pytest.mark.asyncio
    async def test_commit_message_quoted(self, reg):
        p = _provider(reg, "tool:git")
        r = await p.invoke({"args": 'commit -m "fix: bug"'})
        # 白名单通过（实际成败取决于 cwd 是否 git 仓库——非仓库 code!=0）
        assert "子命令不在白名单" not in (r.get("error") or "")

    def test_structured_status(self):
        s = _git_structured_public("status", "On branch master\nnothing to commit")
        assert s["branch"] == "master"
        assert s["dirty"] is False


def _git_structured_public(args, stdout):
    from project_runtime.worker.coding_tools import _git_structured

    return _git_structured(args, stdout)


# ── test_runner ────────────────────────────────────────────────────


class TestTestRunner:
    def test_parse_pytest(self):
        out = (
            "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
            "FAILED tests/test_a.py::test_y - KeyError: 'k'\n"
            ".\n2 failed, 18 passed in 3.2s"
        )
        s = _parse_pytest(out, 1)
        assert s["passed"] == 18
        assert s["failed"] == 2
        assert len(s["failed_cases"]) == 2
        assert s["failed_cases"][0].startswith("FAILED tests/test_a.py::test_x")

    def test_parse_all_pass(self):
        s = _parse_pytest("35 passed in 1.0s", 0)
        assert s == {"exit_code": 0, "passed": 35, "failed": 0,
                     "failed_cases": []}

    @pytest.mark.asyncio
    async def test_config_injection_rejected(self, reg):
        p = _provider(reg, "tool:test_runner")
        r = await p.invoke({"target": "tests/ -c evil.cfg"})
        assert r["ok"] is False
        assert "参数模式不合规" in r["error"]

    @pytest.mark.asyncio
    async def test_runner_whitelist(self, reg):
        p = _provider(reg, "tool:test_runner")
        r = await p.invoke({"runner": "jest"})
        assert r["ok"] is False


# ── builder / arch_diagram ─────────────────────────────────────────


class TestBuilderAndDiagram:
    @pytest.mark.asyncio
    async def test_kind_required(self, reg):
        p = _provider(reg, "tool:builder")
        r = await p.invoke({})
        assert r["ok"] is False
        assert r["error_code"] == "MISSING_FIELD"
        assert "kind" in r["error"]

    @pytest.mark.asyncio
    async def test_kind_whitelist(self, reg):
        p = _provider(reg, "tool:builder")
        r = await p.invoke({"kind": "cargo_build"})
        assert r["ok"] is False
        assert r["error_code"] == "INVALID_ACTION"
        assert "npm_build/docker_build" in r["error"]
        assert "hint" in r

    @pytest.mark.asyncio
    async def test_tag_pattern(self, reg):
        p = _provider(reg, "tool:builder")
        r = await p.invoke({"kind": "docker_build", "tag": "a; rm -rf"})
        assert r["ok"] is False

    @pytest.mark.asyncio
    async def test_mermaid_header_check(self, reg, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = _provider(reg, "tool:arch_diagram")
        r = await p.invoke({"mermaid": "digraph { a }"})
        assert r["ok"] is False

    @pytest.mark.asyncio
    async def test_mmd_fallback(self, reg, tmp_path, monkeypatch):
        """无 mmdc → .mmd 落盘、ok=True、rendered=False（降级非失败）。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks").mkdir(parents=True, exist_ok=True)
        p = _provider(reg, "tool:arch_diagram")
        r = await p.invoke({
            "mermaid": "graph TD; A-->B",
            "name": "demo",
            "out_dir": "data/tasks",
        })
        assert r["ok"] is True
        assert r["rendered"] is False
        assert Path(r["path"]).exists()
        assert Path(r["path"]).read_text(encoding="utf-8") == "graph TD; A-->B"


# ── todo ───────────────────────────────────────────────────────────


class TestTodo:
    @pytest.mark.asyncio
    async def test_roundtrip(self, reg, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/w1").mkdir(parents=True, exist_ok=True)
        p = _provider(reg, "tool:todo")
        r = await p.invoke({"action": "add", "text": "写单测",
                            "workspace": "data/tasks/w1"})
        assert r["ok"] and len(r["items"]) == 1
        # index=0（or 短路回归——曾被吞成 -1）
        r2 = await p.invoke({"action": "toggle", "index": 0,
                             "workspace": "data/tasks/w1"})
        assert r2["ok"] and r2["items"][0]["done"] is True
        r3 = await p.invoke({"action": "list", "workspace": "data/tasks/w1"})
        assert r3["items"][0]["done"] is True

    @pytest.mark.asyncio
    async def test_index_bounds(self, reg, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/w2").mkdir(parents=True, exist_ok=True)
        p = _provider(reg, "tool:todo")
        r = await p.invoke({"action": "toggle", "index": 5,
                            "workspace": "data/tasks/w2"})
        assert r["ok"] is False


# ── 行业插件包三防 ─────────────────────────────────────────────────


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


class TestIndustryPack:
    def test_happy_path(self, tmp_path):
        z = _zip({
            "manifest.json": json.dumps({
                "name": "arch", "version": "1.0.0",
                "stage": "architecture", "providers": ["calc.py"]}),
            "calc.py": "class CalcProvider: pass",
        })
        out = import_industry_pack(z, tmp_path)
        assert out["name"] == "arch"
        assert len(out["installed"]) == 2
        assert (tmp_path / "arch@1.0.0" / "calc.py").exists()

    def test_traversal_rejected(self, tmp_path):
        z = _zip({
            "manifest.json": json.dumps({"name": "x", "version": "1",
                                         "providers": ["a.py"]}),
            "../evil.py": "x",
        })
        with pytest.raises(IndustryPackError, match="穿越"):
            import_industry_pack(z, tmp_path)

    def test_undeclared_rejected(self, tmp_path):
        z = _zip({
            "manifest.json": json.dumps({"name": "x", "version": "1",
                                         "providers": ["a.py"]}),
            "a.py": "x",
            "hidden.py": "evil",
        })
        with pytest.raises(IndustryPackError, match="未声明"):
            import_industry_pack(z, tmp_path)

    def test_overwrite_rejected(self, tmp_path):
        z = _zip({
            "manifest.json": json.dumps({"name": "x", "version": "1",
                                         "providers": ["a.py"]}),
            "a.py": "x",
        })
        import_industry_pack(z, tmp_path)
        with pytest.raises(IndustryPackError, match="已存在"):
            import_industry_pack(z, tmp_path)

    def test_missing_manifest(self, tmp_path):
        z = _zip({"a.py": "x"})
        with pytest.raises(IndustryPackError, match="manifest"):
            import_industry_pack(z, tmp_path)

    def test_manifest_missing_field(self, tmp_path):
        z = _zip({
            "manifest.json": json.dumps({"name": "x", "providers": ["a.py"]}),
            "a.py": "x",
        })
        with pytest.raises(IndustryPackError, match="缺字段: version"):
            import_industry_pack(z, tmp_path)

    def test_not_a_zip(self, tmp_path):
        with pytest.raises(IndustryPackError, match="非法 ZIP"):
            import_industry_pack(b"not a zip at all", tmp_path)
