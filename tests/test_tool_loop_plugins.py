"""20号规范三能力① 接线测试: 工具循环暴露全部 TOOL 插件（§4.3）.

验证:
  [x] 工具面 = web_search + 批次1七件 + 批次3五件（13 个）
  [x] 每个工具描述非空（描述即调用策略）+ parameters 是合法 schema
  [x] TOOL provider 分发: 注册的插件名经 _run_tool_plugin 命中 invoke
  [x] 未注册名返回 None（unknown tool 语义）
  [x] 搜索不可用时工具面仅剩 TOOL 插件（可用性决定可见性）
"""

import pytest

from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.worker.coding_tools import register_batch3_tools
from project_runtime.worker.tools import register_worker_tools


def _full_registry() -> ExtensionRegistry:
    from project_runtime.research.providers import register_search_providers

    reg = ExtensionRegistry()
    register_search_providers(reg)
    register_worker_tools(reg)
    register_batch3_tools(reg)
    return reg


def _worker_with(registry):
    from project_runtime.worker.llm_worker import LLMWorkerAdapter

    w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
    return w, registry


_EXPECTED_TOOLS = {
    "web_search",
    "tool:webfetch", "tool:read_file", "tool:list_dir", "tool:write_file",
    "tool:shell",
    "tool:code_search", "tool:slide_generator",
    "tool:git", "tool:test_runner", "tool:builder",
    "tool:arch_diagram", "tool:todo",
}


class TestToolLoopExposure:
    def test_all_tools_exposed(self, monkeypatch):
        """工具面 = 搜索 + 十二插件（13 个——三能力① 完整面）。"""
        reg = _full_registry()
        import project_runtime.extension.shared as shared_mod

        monkeypatch.setattr(shared_mod, "_registry", reg)
        w, _ = _worker_with(reg)
        tools = w._search_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == _EXPECTED_TOOLS, (
            f"工具面 {len(names)} 个: {sorted(names)}"
        )

    def test_every_tool_has_description_and_schema(self, monkeypatch):
        """描述即调用策略: 每工具描述非空 + parameters 合法。"""
        reg = _full_registry()
        import project_runtime.extension.shared as shared_mod

        monkeypatch.setattr(shared_mod, "_registry", reg)
        w, _ = _worker_with(reg)
        for t in w._search_tools():
            fn = t["function"]
            assert fn["description"] and len(fn["description"]) > 20, (
                f"{fn['name']} 描述过短——模型无法自决"
            )
            assert fn["parameters"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_tool_plugin_dispatch(self, monkeypatch, tmp_path):
        """TOOL 分发: tool:todo 经 _run_tool_plugin 真执行。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data/tasks/w").mkdir(parents=True, exist_ok=True)
        reg = _full_registry()
        import project_runtime.extension.shared as shared_mod

        monkeypatch.setattr(shared_mod, "_registry", reg)
        w, _ = _worker_with(reg)
        result = await w._run_tool_plugin(
            "tool:todo",
            {"action": "add", "text": "接线测试", "workspace": "data/tasks/w"},
        )
        assert result is not None and result["ok"] is True
        assert any("接线测试" in (i or {}).get("text", "")
                   for i in result["items"])

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_none(self, monkeypatch):
        reg = _full_registry()
        import project_runtime.extension.shared as shared_mod

        monkeypatch.setattr(shared_mod, "_registry", reg)
        w, _ = _worker_with(reg)
        assert await w._run_tool_plugin("tool:nope", {}) is None

    def test_no_search_tools_still_expose_plugins(self, monkeypatch):
        """搜索不可用（无 RESEARCH provider）→ 工具面仅剩 TOOL 插件。"""
        reg = ExtensionRegistry()
        register_worker_tools(reg)
        register_batch3_tools(reg)
        import project_runtime.extension.shared as shared_mod

        monkeypatch.setattr(shared_mod, "_registry", reg)
        w, _ = _worker_with(reg)
        tools = w._search_tools()
        names = {t["function"]["name"] for t in tools}
        assert "web_search" not in names
        assert "tool:git" in names  # 插件照常可见
