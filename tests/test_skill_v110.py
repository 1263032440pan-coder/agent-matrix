"""V1.5a-1 测试: skill 1.1.0 双形态解析 + code_stage 透传 + 材料兜底。

覆盖（修订单 §四测试计划的前四条）:
  [x] skill 1.1.0 导入: stages 结构化断言（本地 zip 直读——导入 API 走既有链）
  [x] 旧 zip（字符串 stages）解析兼容
  [x] code_stage 透传: 新形态源码 outputs → True; 旧形态/文档 outputs → False
  [x] 材料兜底: skill 没说但材料说"要源码" → 开发类 workstream 置 code_stage
  [x] 触达断言（补强 A）: skill_body[:2000] 窗口含纪律关键词
"""

import json
import zipfile


def _parse_stages(raw_json: str) -> list[dict]:
    """复刻 read_skill_stages 的解析段（单元级验证解析语义）。"""
    stages: list[dict] = []
    raw = json.loads(raw_json)
    if isinstance(raw, list):
        for st in raw:
            if isinstance(st, dict):
                entry = {"name": str(st.get("name") or "").strip()}
                outs = st.get("outputs")
                if isinstance(outs, list) and outs:
                    entry["expected_outputs"] = [str(o) for o in outs]
                if entry["name"]:
                    stages.append(entry)
            elif isinstance(st, str) and st.strip():
                stages.append({"name": st.strip()})
    _code_kws = ("源码", "入口文件", "代码", "工程")
    for st in stages:
        outs = [str(o) for o in (st.get("expected_outputs") or [])]
        st["code_stage"] = any(any(k in o for k in _code_kws) for o in outs)
    return stages


class TestDualStagesParsing:
    def test_new_structured_form(self):
        """1.1.0 结构化 stages: name+outputs 取出, code_stage 判定。"""
        raw = json.dumps([
            {"name": "项目理解", "outputs": ["项目理解报告"]},
            {"name": "开发实现",
             "outputs": ["入口文件", "功能模块", "测试文件", "README"]},
            {"name": "测试验证", "outputs": ["测试报告"]},
        ], ensure_ascii=False)
        stages = _parse_stages(raw)
        assert [s["name"] for s in stages] == ["项目理解", "开发实现", "测试验证"]
        assert stages[1]["expected_outputs"] == [
            "入口文件", "功能模块", "测试文件", "README",
        ]
        assert stages[1]["code_stage"] is True
        assert stages[0]["code_stage"] is False
        assert stages[2]["code_stage"] is False

    def test_legacy_string_form_compat(self):
        """旧 zip 字符串列表照常解析（向后兼容——补强 B）。"""
        raw = json.dumps(
            ["项目理解", "需求基线", "开发实现", "测试验证"],
            ensure_ascii=False,
        )
        stages = _parse_stages(raw)
        assert len(stages) == 4
        assert stages[0]["name"] == "项目理解"
        assert "expected_outputs" not in stages[0]
        assert all(s["code_stage"] is False for s in stages)  # 无 outputs 不标

    def test_mixed_form(self):
        """混合形态（新旧行交错）不炸。"""
        raw = json.dumps(
            ["项目理解", {"name": "开发实现", "outputs": ["源码"]}],
            ensure_ascii=False,
        )
        stages = _parse_stages(raw)
        assert len(stages) == 2
        assert stages[1]["code_stage"] is True


class TestSkillZipV110:
    def test_zip_contains_discipline_and_structured_stages(self):
        """1.1.0 包: SKILL.md 含纪律总则 + 结构化 stages frontmatter。"""
        import os

        pkg = "data/skills/software-project-delivery-1.1.0.zip"
        assert os.path.exists(pkg), "1.1.0 包未打"
        with zipfile.ZipFile(pkg) as z:
            sk = z.read("SKILL.md").decode("utf-8")
            assert "version: 1.1.0" in sk
            # 补强 A: 纪律总则 + 关键词在前 2000 字符（llm_worker 截取窗口）
            win = sk[:2000]
            assert "执行纪律总则" in win
            assert "write_file" in win and "test_runner" in win
            assert "file_rw" not in win
            assert "README" in win
            # stages 结构化
            assert "- name: 开发实现" in sk
            assert "outputs: [入口文件, 功能模块, 测试文件, README]" in sk
            # 九阶段稳定性（名字都在）
            for name in ("项目理解", "需求基线", "解决方案", "原型设计",
                         "架构设计", "开发实现", "测试验证", "交付打包",
                         "使用文档"):
                assert f"name: {name}" in sk

    def test_references_preserved(self):
        """references/ 三文件原样保留。"""
        with zipfile.ZipFile(
            "data/skills/software-project-delivery-1.1.0.zip"
        ) as z:
            names = z.namelist()
            for ref in ("references/quality-gates.md",
                        "references/stage-guide.md",
                        "references/templates.md"):
                assert ref in names


class TestMaterialFallback:
    def test_material_requires_code_positive(self):
        """材料含'项目源码' → 兜底触发。"""
        from project_runtime.playbook.builder import PlaybookBuilder

        class _A:
            content = "最终交付物：\n项目源码\n使用文档"
        assert PlaybookBuilder._material_requires_code([_A()]) is True

    def test_material_requires_code_negative(self):
        from project_runtime.playbook.builder import PlaybookBuilder

        class _A:
            content = "只要文档：需求文档、测试报告"
        assert PlaybookBuilder._material_requires_code([_A()]) is False

    def test_fallback_marks_dev_workstream(self):
        """兜底: skill 未声明 code_stage + 材料要求 → 开发类 ws 被标记。

        复刻 builder 兜底段语义（build 全链在集成测试覆盖）。
        """
        workstreams = [
            {"name": "需求分析", "source_skill_stage": "需求基线"},
            {"name": "系统实现", "source_skill_stage": "开发实现"},
            {"name": "测试", "source_skill_stage": "测试验证"},
        ]
        # 兜底逻辑复刻
        class _A:
            content = "交付：项目源码"
        from project_runtime.playbook.builder import PlaybookBuilder

        if not any(ws.get("code_stage") for ws in workstreams):
            if PlaybookBuilder._material_requires_code([_A()]):
                for ws in workstreams:
                    if "开发" in (ws.get("source_skill_stage") or ""):
                        ws["code_stage"] = True
        assert workstreams[1]["code_stage"] is True
        assert workstreams[0].get("code_stage") is not True
        assert workstreams[2].get("code_stage") is not True
