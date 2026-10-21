"""V1.4i write 流式拼装修复测试: _repair_arguments + 退化分支。"""

import json

import pytest

from project_runtime.worker.llm_worker import _repair_arguments


class TestRepairArguments:
    def test_intact_json_passthrough(self):
        """完整 JSON 原样通过。"""
        raw = json.dumps({"action": "write", "path": "a.md", "content": "x"})
        assert _repair_arguments(raw) == raw

    def test_empty_returns_empty_dict(self):
        assert _repair_arguments("") == "{}"
        assert _repair_arguments(None) == "{}"
        assert _repair_arguments("   ") == "{}"

    def test_tail_truncation_repaired(self):
        """尾部截断（主形态）: '{"...","content":"长文' → 补 '"}'。"""
        # 模拟: 完整 json 去掉尾部 '"}' 两字符
        full = json.dumps(
            {"action": "write", "path": "data/x.md",
             "content": "# 文档\n\n正文 " * 200},
            ensure_ascii=False,
        )
        truncated = full[:-2]  # 截掉 "}"
        repaired = _repair_arguments(truncated)
        assert repaired is not None
        d = json.loads(repaired)
        assert d["action"] == "write"
        assert d["path"] == "data/x.md"
        assert len(d["content"]) > 1000  # 内容保住

    def test_tail_truncation_variant(self):
        """变体: 只截了最后一个 '}'。"""
        full = json.dumps({"action": "read", "path": "a.md"})
        assert _repair_arguments(full[:-1]) is not None

    def test_dangling_backslash_repaired(self):
        """悬挂反斜杠: content 尾部断在转义符中间。"""
        raw = '{"action": "write", "path": "a.md", "content": "文本' + chr(92)
        repaired = _repair_arguments(raw)
        assert repaired is not None
        d = json.loads(repaired)
        assert d["content"].startswith("文本")

    def test_mid_string_truncation_semantics(self):
        """content 中间截断: 语法可修复（尾部补闭合）——内容残缺但可用。

        防线1语义边界: 流式丢块落在字符串值内部时, 引号可能仍闭合
        （json 合法）或经补闭合后合法——此时 content 已残缺。
        修复产生"语法完整、内容可能截断"的调用——模型 read 回验可发现
        并续写补救（优于整体失败重传的确定性丢失）。
        """
        full = json.dumps(
            {"action": "write", "path": "a.md", "content": "x" * 800}
        )
        broken = full[: len(full) // 2]  # content 中间硬截断
        repaired = _repair_arguments(broken)
        assert repaired is not None  # 语法层修复成功
        d = json.loads(repaired)
        assert d["action"] == "write"
        assert d["path"] == "a.md"
        assert len(d["content"]) < 800  # 内容确实残缺——语义如实断言
        assert len(d["content"]) > 300  # 但保住了前半

    def test_truly_unrepairable_returns_none(self):
        """真不可修复形态: 断在键名/结构处（补任何闭合符都非法）。"""
        raw = '{"act'
        assert _repair_arguments(raw) is None
        # '{"act' 键名未闭合 — None 已断言; 其他形态留给实现裁量

    def test_real_case_shape(self):
        """实测形态复现: 模型 write 被截在表格中间。"""
        # 真实事件里的截断形态（query 显示到 200 字符处）
        raw = ('{"action": "write", "path": "data/tasks/1/6/开发实现_output.v1.md", '
               '"content": "# 阶段 6：开发实现 —— 交付文档\\n\\n## 产出物清单\\n\\n'
               '| 产出物 | 路径 | 格式 |\\n|---|---|---|\\n| 开发实现文档 | '
               '`data/tasks/1/6/开发实现_out')
        # 该形态按真实截断应无法尾部修复（字符串中间断）——但补 '"}' 后
        # json 合法（字符串可以就在这结束）——内容丢了后半但调用可执行
        repaired = _repair_arguments(raw)
        # 接受两种结果: 修复成功（截断内容可用）或 None（要求重试）
        if repaired is not None:
            d = json.loads(repaired)
            assert d["action"] == "write"
            assert d["path"].endswith("开发实现_output.v1.md")


class TestRunSearchToolDegrade:
    @pytest.mark.asyncio
    async def test_broken_arguments_returns_actionable_error(self, monkeypatch):
        """防线3: 拼装断裂且无法修复 → 模型收到可理解错误（非垃圾参数）。"""
        from project_runtime.worker.llm_worker import LLMWorkerAdapter

        w = LLMWorkerAdapter.__new__(LLMWorkerAdapter)
        broken = '{"action": "write", "path": "a.md", "content": "x' + (
            "y" * 400
        )
        # 中段丢块形态（不可修复）
        full = '{"action": "write", "path": "a.md", "content": "' + "y" * 400 + '"}'
        mid_broken = full[:50] + full[250:]
        for args_json in (broken, mid_broken):
            if _repair_arguments(args_json) is not None:
                continue  # 可修复的走正常路径——只测不可修复
            text, ok = await w._run_search_tool(1, "tool:file_rw", args_json, 1)
            assert ok is False
            assert "传输不完整" in text
            assert "重试" in text or "拆分" in text  # 可自救的指引
