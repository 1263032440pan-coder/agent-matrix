"""web_search TOOL 门面（方案 §13.3）— 后端仍为 RESEARCH Provider。"""

from __future__ import annotations

from typing import Any

from project_runtime.extension.registry import CapabilityType


class WebSearchToolProvider:
    """模型可见的 web_search；health 委托 RESEARCH 可用性。"""

    name = "tool:web_search"
    capability = CapabilityType.TOOL
    permission = "READ_ONLY"
    priority = 200
    enabled = True
    # LLM 侧保留裸名，兼容白名单与既有提示词
    llm_name = "web_search"

    def tool_card(self) -> dict[str, Any]:
        return {
            "summary": (
                "搜索互联网获取当前任务所需信息。"
                "每次调用消耗搜索配额，能从已有材料推断的优先推断。"
            ),
            "when_not": (
                "任务材料（上下文/契约/方法指导）已自足时不要调用。"
            ),
            "when": (
                "缺外部知识时调用：特定技术/框架 API 与版本兼容性、"
                "行业最佳实践、明确客观事实查证。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询（自然语言，中文或英文）",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "结果数（默认 6，最大 10）",
                    },
                },
                "required": ["query"],
            },
            "detail": {},
            "error_sections": {},
            "needs_workspace_root": False,
        }

    def tool_schema(self) -> dict:
        from project_runtime.worker.tool_card import schema_from_tool_card

        return schema_from_tool_card(
            self.name, self.tool_card(), llm_name=self.llm_name,
        )

    def is_usable(self, registry=None) -> bool:
        """拼面用同步检查：同注册表内是否有 enabled RESEARCH。"""
        if registry is None:
            from project_runtime.extension.shared import shared_registry

            registry = shared_registry()
        return any(
            i.enabled and i.provider is not None
            for i in registry.list_providers(CapabilityType.RESEARCH)
        )

    async def health(self) -> bool:
        return self.is_usable()

    async def invoke(self, payload: dict) -> dict:
        query = str(payload.get("query") or "").strip()
        if not query:
            from project_runtime.worker.tools import tool_error

            return tool_error("MISSING_FIELD", "web_search: query 必填")
        n = min(int(payload.get("num_results") or 6), 10)
        from project_runtime.extension.shared import shared_registry

        registry = shared_registry()
        results: list[dict] = []
        last_err = ""
        for info in registry.list_providers(CapabilityType.RESEARCH):
            if not info.enabled or info.provider is None:
                continue
            try:
                out = await info.provider.invoke(
                    {"query": query, "num_results": n}
                )
                if isinstance(out, list) and out:
                    results = out
                    break
                if isinstance(out, dict) and out.get("results"):
                    results = list(out["results"])
                    break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)[:200]
                continue
        if not results:
            return {
                "ok": False,
                "error": last_err or "搜索失败或无结果",
                "results": [],
            }
        return {"ok": True, "results": results}
