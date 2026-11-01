"""运行动态 API：事件 kind 映射与 activity 节流。"""

import asyncio

from apps.runtime.api.projects import _THINKING_KIND, _THINKING_STREAM_TYPES


def test_thinking_stream_includes_activity_and_tools():
    assert "AGENT_ACTIVITY" in _THINKING_STREAM_TYPES
    assert "LLM_TOOL_CALL" in _THINKING_STREAM_TYPES
    assert _THINKING_KIND["AGENT_ACTIVITY"] == "activity"
    assert _THINKING_KIND["LLM_ANSWER_CHUNK"] == "answer"
    assert _THINKING_KIND["LLM_TOOL_CALL"] == "tool"


def test_emit_activity_throttle(monkeypatch):
    from project_runtime.event import activity as act

    calls: list[str] = []

    class _EE:
        async def record(self, *a, **k):
            calls.append(k.get("message") or "")
            return 1

    monkeypatch.setattr(
        "project_runtime.event.EventEngine", lambda: _EE(),
    )
    act._last.clear()

    async def _run():
        await act.emit_activity(1, "等待模型输出…", phase="llm_wait")
        await act.emit_activity(1, "等待模型输出…", phase="llm_wait")
        await act.emit_activity(
            1, "质量关卡", phase="gate", throttle=False,
        )

    asyncio.run(_run())
    assert len(calls) == 2
    assert "等待" in calls[0]
    assert "关卡" in calls[1]
