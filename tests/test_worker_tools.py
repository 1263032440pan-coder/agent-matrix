"""20号规范批次 1: 底座插件测试（全离线）.

覆盖:
  [x] file_rw 工作区禁闭（越界拒绝/不存在 404 语义/合法读）
  [x] shell 三防（白名单/参数模式/注入黑名单/cd 逃逸/git 只读子命令）
  [x] code_search 正则命中+glob+越界拒绝
  [x] slide_generator md→pptx（读回验证页数/标题）+ 文件名清洗
  [x] webfetch URL 校验（非 http 拒绝）
  [x] register_worker_tools 组合根（TOOL capability 注册齐）
"""

import os

import pytest

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


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """临时工作区（data/tasks 禁闭根重定向到 tmp）。"""
    root = tmp_path / "tasks"
    root.mkdir()
    import project_runtime.worker.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_WORKSPACE_ROOT", root.resolve())
    (root / "p1" / "t1").mkdir(parents=True)
    (root / "p1" / "t1" / "doc.md").write_text("# 内容\nhello world", encoding="utf-8")
    return root


@pytest.fixture
def providers(workspace):
    from project_runtime.worker.tools import (
        CodeSearchProvider,
        FileRwProvider,
        ShellProvider,
        SlideGeneratorProvider,
        WebFetchProvider,
    )

    return {
        "frw": FileRwProvider(),
        "shell": ShellProvider(),
        "search": CodeSearchProvider(),
        "slide": SlideGeneratorProvider(),
        "fetch": WebFetchProvider(),
    }


class TestFileRw:
    @pytest.mark.asyncio
    async def test_read_within_workspace(self, providers, workspace):
        r = await providers["frw"].invoke({
            "action": "read", "path": str(workspace / "p1" / "t1" / "doc.md")
        })
        assert r["ok"] and "hello world" in r["content"]

    @pytest.mark.asyncio
    async def test_traversal_rejected(self, providers):
        r = await providers["frw"].invoke({
            "action": "read", "path": "C:/Windows/win.ini"
        })
        assert not r["ok"] and "越界" in r["error"]

    @pytest.mark.asyncio
    async def test_missing_file(self, providers, workspace):
        r = await providers["frw"].invoke({
            "action": "read", "path": str(workspace / "p1" / "nope.md")
        })
        assert not r["ok"] and "不存在" in r["error"]

    @pytest.mark.asyncio
    async def test_list_dir(self, providers, workspace):
        r = await providers["frw"].invoke({
            "action": "list", "path": str(workspace / "p1" / "t1")
        })
        assert r["ok"] and "doc.md" in r["items"]


class TestShell:
    @pytest.mark.asyncio
    async def test_allowed_command(self, providers, workspace):
        r = await providers["shell"].invoke({
            "command": "echo hello",
            "workspace": str(workspace / "p1" / "t1"),
        })
        assert r["ok"] and "hello" in r["stdout"]

    @pytest.mark.asyncio
    async def test_unknown_command_rejected(self, providers, workspace):
        r = await providers["shell"].invoke({
            "command": "curl http://evil", "workspace": str(workspace)
        })
        assert not r["ok"] and "白名单" in r["error"]

    @pytest.mark.asyncio
    async def test_dangerous_pattern_rejected(self, providers, workspace):
        """rm -rf 被拒（白名单层 rm 不在单命令白名单；即使别名绕过也撞注入黑名单）。"""
        for cmd in ("rm -rf /", "del /s /q C:\\"):
            r = await providers["shell"].invoke({
                "command": cmd, "workspace": str(workspace)
            })
            assert not r["ok"], f"{cmd} 应被拒绝"

    @pytest.mark.asyncio
    async def test_pytest_config_injection_rejected(self, providers, workspace):
        """P2-1 返修条款: pytest 禁 -c/--rootdir（配置注入）。"""
        r = await providers["shell"].invoke({
            "command": "pytest -c evil.ini", "workspace": str(workspace)
        })
        assert not r["ok"]

    @pytest.mark.asyncio
    async def test_cwd_escape_rejected(self, providers, workspace):
        """P2-1: cd 绝对路径 = 工作区逃逸（cd 不在白名单即拒；注入黑名单双保险）。"""
        r = await providers["shell"].invoke({
            "command": "cd C:/ && dir", "workspace": str(workspace)
        })
        assert not r["ok"]

    @pytest.mark.asyncio
    async def test_git_write_subcommand_rejected(self, providers, workspace):
        """git 只读子命令白名单——push/reset 拒绝。"""
        for cmd in ("git push origin master", "git reset --hard"):
            r = await providers["shell"].invoke({
                "command": cmd, "workspace": str(workspace)
            })
            assert not r["ok"], f"{cmd} 应被拒绝"

    @pytest.mark.asyncio
    async def test_git_read_allowed(self, providers, workspace):
        """git 只读子命令过白名单（tmp 非 git 仓库返回码非 0——只验不拒绝）。"""
        r = await providers["shell"].invoke({
            "command": "git status", "workspace": str(workspace)
        })
        assert "白名单" not in r.get("error", ""), "git status 不应被白名单拒绝"
        assert "code" in r  # 执行到达（返回码可查）


class TestCodeSearch:
    @pytest.mark.asyncio
    async def test_pattern_hit(self, providers, workspace):
        r = await providers["search"].invoke({
            "pattern": "hello",
            "glob": "*.md",
            "scope": str(workspace),
        })
        assert r["ok"] and r["count"] >= 1
        assert any("doc.md" in h["file"] for h in r["hits"])

    @pytest.mark.asyncio
    async def test_scope_traversal_rejected(self, providers):
        r = await providers["search"].invoke({
            "pattern": "x", "scope": "C:/Windows"
        })
        assert not r["ok"] and "越界" in r["error"]

    @pytest.mark.asyncio
    async def test_bad_regex(self, providers, workspace):
        r = await providers["search"].invoke({
            "pattern": "([unclosed", "scope": str(workspace)
        })
        assert not r["ok"]


class TestSlideGenerator:
    @pytest.mark.asyncio
    async def test_md_to_pptx(self, providers, workspace):
        md = (
            "# 项目方案汇报\n\n"
            "## 背景\n- 要点一\n- 要点二\n\n"
            "## 方案\n这是方案内容段落。\n\n"
            "## 结尾\n- 谢谢"
        )
        out = workspace / "p1" / "t1"
        r = await providers["slide"].invoke({
            "markdown": md, "workspace": str(out), "filename": "方案.pptx",
        })
        assert r["ok"], r
        assert r["slides"] >= 4  # 标题页+至少 3 节页
        # 读回验证
        from pptx import Presentation

        prs = Presentation(r["path"])
        assert len(prs.slides._sldIdLst) == r["slides"]  # 返回值与读回一致
        first_title = prs.slides[0].shapes.title.text
        assert "项目方案汇报" in first_title
        os.unlink(r["path"])

    @pytest.mark.asyncio
    async def test_filename_sanitized(self, providers, workspace):
        r = await providers["slide"].invoke({
            "markdown": "# T\n\n## A\n- b",
            "workspace": str(workspace),
            "filename": "坏/名:字?.pptx",
        })
        assert r["ok"]
        assert "/" not in os.path.basename(r["path"])
        os.unlink(r["path"])

    @pytest.mark.asyncio
    async def test_empty_md_rejected(self, providers, workspace):
        r = await providers["slide"].invoke({
            "markdown": "  ", "workspace": str(workspace),
        })
        assert not r["ok"]


class TestWebFetch:
    @pytest.mark.asyncio
    async def test_non_http_rejected(self, providers):
        r = await providers["fetch"].invoke({"url": "ftp://x"})
        assert not r["ok"]

    @pytest.mark.asyncio
    async def test_empty_url(self, providers):
        r = await providers["fetch"].invoke({"url": ""})
        assert not r["ok"]


class TestRegistration:
    def test_register_worker_tools(self):
        from project_runtime.extension.registry import ExtensionRegistry
        from project_runtime.plugin.capability import CapabilityType
        from project_runtime.worker.tools import register_worker_tools

        reg = ExtensionRegistry()
        register_worker_tools(reg)
        names = {i.name for i in reg.list_providers(CapabilityType.TOOL)}
        assert names == {
            "tool:webfetch", "tool:file_rw", "tool:shell",
            "tool:code_search", "tool:slide_generator",
        }
