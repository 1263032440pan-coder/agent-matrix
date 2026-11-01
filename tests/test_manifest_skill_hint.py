"""SOFTWARE_DEV 清单基线提示：三件必含，不得「勿编造」冲掉 PPT。"""

from project_runtime.procedure.delivery_manifest import (
    manifest_skill_hint_for_profile,
)


def test_software_dev_hint_requires_three_baseline():
    h = manifest_skill_hint_for_profile("SOFTWARE_DEV")
    assert "pptx" in h.lower() or "PPT" in h
    assert "测试" in h
    assert "代码" in h or "源码" in h or "README" in h
    assert "不要编造" not in h
    assert "必含" in h or "必然" in h


def test_not_software_hint_empty():
    assert manifest_skill_hint_for_profile("NOT_SOFTWARE") == ""
    assert manifest_skill_hint_for_profile("") == ""
    assert manifest_skill_hint_for_profile(None) == ""
