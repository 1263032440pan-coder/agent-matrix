"""20号规范批次 1: 底座插件测试（全离线）.

覆盖:
  [x] read_file / list_dir 工作区禁闭（越界拒绝/不存在 404 语义/合法读）
  [x] shell 三防（白名单/参数模式/注入黑名单/cd 逃逸/git 只读子命令）
  [x] code_search 正则命中+glob+越界拒绝
  [x] office_pptx md→pptx（读回验证页数/标题）
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
    from project_runtime.office.providers import OfficePptxProvider
    from project_runtime.worker.tools import (
        CodeSearchProvider,
        ListDirProvider,
        ReadFileProvider,
        ShellProvider,
        WebFetchProvider,
    )

    return {
        "read": ReadFileProvider(),
        "list": ListDirProvider(),
        "shell": ShellProvider(),
        "search": CodeSearchProvider(),
        "slide": OfficePptxProvider(),
        "fetch": WebFetchProvider(),
    }


class TestFileRw:
    @pytest.mark.asyncio
    async def test_read_within_workspace(self, providers, workspace):
        r = await providers["read"].invoke({
            "path": str(workspace / "p1" / "t1" / "doc.md")
        })
        assert r["ok"] and "hello world" in r["content"]

    @pytest.mark.asyncio
    async def test_traversal_rejected(self, providers):
        r = await providers["read"].invoke({"path": "C:/Windows/win.ini"})
        assert not r["ok"] and "越界" in r["error"]

    @pytest.mark.asyncio
    async def test_missing_file(self, providers, workspace):
        r = await providers["read"].invoke({
            "path": str(workspace / "p1" / "nope.md")
        })
        assert not r["ok"] and "不存在" in r["error"]

    @pytest.mark.asyncio
    async def test_list_dir(self, providers, workspace):
        r = await providers["list"].invoke({
            "path": str(workspace / "p1" / "t1")
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

    @pytest.mark.asyncio
    async def test_windows_head_and_grep(self, providers, workspace):
        """SYS-160: Windows 上 head 内建、grep 映射 findstr。"""
        import os

        import pytest as _pytest

        if os.name != "nt":
            _pytest.skip("Windows 映射")
        ws = workspace / "p1" / "t1"
        h = await providers["shell"].invoke({
            "command": "head -n 1 doc.md",
            "workspace": str(ws),
        })
        assert h["ok"] and "内容" in h["stdout"]
        g = await providers["shell"].invoke({
            "command": "grep hello doc.md",
            "workspace": str(ws),
        })
        assert g["ok"] and "hello" in g.get("stdout", "")


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
            "markdown": md,
            "path": "方案.pptx",
            "__ws_root": str(out),
        })
        assert r["ok"], r
        assert r["slides"] >= 4  # 标题页+至少 3 节页
        # 读回验证
        from pptx import Presentation

        prs = Presentation(r["abs_path"])
        assert len(prs.slides._sldIdLst) == r["slides"]  # 返回值与读回一致
        first_title = prs.slides[0].shapes.title.text
        assert "项目方案汇报" in first_title
        os.unlink(r["abs_path"])

    @pytest.mark.asyncio
    async def test_filename_sanitized(self, providers, workspace):
        r = await providers["slide"].invoke({
            "markdown": "# T\n\n## A\n- b",
            "path": "坏/名:字?.pptx",
            "__ws_root": str(workspace),
        })
        assert r["ok"], r
        # 非法字符清洗；/ 保留为子目录
        assert (workspace / "坏" / "名_字_.pptx").exists()
        os.unlink(r["abs_path"])

    @pytest.mark.asyncio
    async def test_empty_md_rejected(self, providers, workspace):
        r = await providers["slide"].invoke({
            "markdown": "  ",
            "path": "empty.pptx",
            "__ws_root": str(workspace),
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
        assert "tool:office_pptx" in names
        assert "tool:office_docx" in names
        assert "tool:image_raster" in names
        assert "tool:slide_generator" not in names
        for base in (
            "tool:webfetch", "tool:read_file", "tool:list_dir",
            "tool:write_file", "tool:append_file", "tool:shell",
            "tool:code_search",
        ):
            assert base in names


# ═══ office_pptx 路径合同 ═══════════════════════════════════════

class TestSlideGenConfinement:
    @pytest.mark.asyncio
    async def test_ws_root_forces_task_workspace(self, tmp_path,
                                                 monkeypatch):
        """__ws_root 注入: path 相对任务工作区。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/2/18").mkdir(parents=True)
        from project_runtime.office.providers import OfficePptxProvider

        prov = OfficePptxProvider()
        r = await prov.invoke({
            "markdown": "# T\n## 内容\n- a",
            "path": "方案.pptx",
            "__ws_root": str(tmp_path / "data/tasks/2/18"),
        })
        assert r["ok"] is True, r
        assert (tmp_path / "data/tasks/2/18/方案.pptx").exists()
        assert not (tmp_path / "data/tasks/2/方案.pptx").exists()

    @pytest.mark.asyncio
    async def test_subdir_path_preserved(self, tmp_path, monkeypatch):
        """子目录分隔符保留（不再把 / 洗成 _）。"""
        monkeypatch.chdir(tmp_path)
        ws = tmp_path / "data/tasks/1/1"
        ws.mkdir(parents=True)
        from project_runtime.office.providers import OfficePptxProvider

        r = await OfficePptxProvider().invoke({
            "markdown": "# T\n## 内容\n- a",
            "path": "deliverables/slides/方案.pptx",
            "__ws_root": str(ws),
        })
        assert r["ok"], r
        assert (ws / "deliverables" / "slides" / "方案.pptx").exists()
        assert r["path"] == "deliverables/slides/方案.pptx"

    def test_schema_has_path(self):
        from project_runtime.worker.tool_schemas import OFFICE_PPTX_SCHEMA

        assert "path" in OFFICE_PPTX_SCHEMA["parameters"]["properties"]


# ═══ 返修单 V3 批D: PPT 按节建页 / Schema 方言 / 归因提示 ══════════

_DOC_7_SECTIONS = """# 政务项目方案汇报

## 背景与目标
本项目面向市级政务服务平台改造。
改造目标是统一入口与材料复用。

## 现状问题
- 多系统登录
- 材料重复提交
- 审批链路不透明

## 总体方案
采用统一身份认证与一件事编排。
数据中台承接共享交换。

## 建设内容
| 模块 | 说明 |
| --- | --- |
| 门户 | 统一入口 |
| 编排 | 一件事流程 |

## 实施计划
```
阶段一 调研
阶段二 建设
阶段三 验收
```
按季度推进里程碑。

## 风险与应对
- 数据质量风险 → 治理专项
- 部门协同风险 → 联席机制

## 预期成效
材料少交、流程少跑、结果可查。
"""


class TestSlideGeneratorV3BatchD:
    """SYS-082/083/084: 节级分页 + 方言 schema + 页数归因。"""

    @pytest.mark.asyncio
    async def test_pages_equal_sections_plus_cover(self, providers, workspace):
        """文档形态 md → 页数 = 二级标题节数 + 封面；零空壳。"""
        out = workspace / "p1" / "t1"
        r = await providers["slide"].invoke({
            "markdown": _DOC_7_SECTIONS,
            "path": "方案.pptx",
            "__ws_root": str(out),
        })
        assert r["ok"], r
        # 1 封面 + 7 节 = 8；不得炸成数十页
        assert r["slides"] == 8, r
        assert r["non_empty_slides"] == 8, r
        assert r.get("has_title_text") is True
        from pptx import Presentation

        prs = Presentation(r["abs_path"])
        assert len(list(prs.slides)) == 8
        os.unlink(r["abs_path"])

    @pytest.mark.asyncio
    async def test_plain_lines_merge_into_section(self, providers, workspace):
        """非子弹普通行归并进当前节页，不得每行一页。"""
        md = (
            "# 封面\n\n"
            "## 唯一一节\n"
            "第一段说明文字。\n"
            "第二段继续说明。\n"
            "第三段还是说明。\n"
            "- 要点甲\n"
            "- 要点乙\n"
        )
        r = await providers["slide"].invoke({
            "markdown": md,
            "path": "p1/t1/merge.pptx",
            "__ws_root": str(workspace),
        })
        assert r["ok"], r
        assert r["slides"] == 2  # 封面 + 1 节
        os.unlink(r["abs_path"])

    @pytest.mark.asyncio
    async def test_hr_is_explicit_page_break(self, providers, workspace):
        """`---` 为显式分页符（对齐 Marp/md2pptx），不把 fence 当页。"""
        md = (
            "# 封面\n\n"
            "## 甲\n"
            "甲内容\n"
            "\n---\n\n"
            "续页无新二级标题也应成页\n"
            "```\ncode block\n```\n"
            "代码后段落\n"
        )
        r = await providers["slide"].invoke({
            "markdown": md,
            "path": "p1/t1/hr.pptx",
            "__ws_root": str(workspace),
        })
        assert r["ok"], r
        # 封面 + 甲 + --- 后一页 = 3；代码块不另起页
        assert r["slides"] == 3, r
        os.unlink(r["abs_path"])

    @pytest.mark.asyncio
    async def test_skew_hint_when_pages_explode(self, providers, workspace):
        """页数相对二级标题偏差>2倍 → stdout 含归因提示。"""
        # 用大量 --- 人为制造页数膨胀（转换器合法分页但仍偏斜）
        body = "## 仅一节\n内容\n" + "\n---\n续\n" * 10
        md = "# 封面\n\n" + body
        r = await providers["slide"].invoke({
            "markdown": md,
            "path": "p1/t1/skew.pptx",
            "__ws_root": str(workspace),
        })
        assert r["ok"], r
        assert r["slides"] > 2 * 1  # h2=1
        assert "可能未按一节一页" in (r.get("stdout") or "")
        os.unlink(r["abs_path"])

    def test_schema_teaches_slide_dialect(self):
        """office_pptx description 引导 JSON/markdown，禁止 write_file。"""
        from project_runtime.worker.tool_schemas import OFFICE_PPTX_SCHEMA

        blob = OFFICE_PPTX_SCHEMA["description"]
        assert "pptx" in blob.lower()
        assert "write_file" in blob or "spec" in blob
