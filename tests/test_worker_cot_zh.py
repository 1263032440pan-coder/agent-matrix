"""Worker 思维链语言：role_prompt / 任务尾指令须强制中文推理。"""

from project_runtime.worker.llm_worker import (
    _WORKER_ROLE_PROMPT,
    _WORKER_TASK_TRAILER,
)


def test_worker_prompts_require_chinese_cot():
    assert "思维链一律使用中文" in _WORKER_ROLE_PROMPT
    assert "思维链（reasoning）一律使用中文" in _WORKER_TASK_TRAILER
    assert "不要输出思考过程" in _WORKER_TASK_TRAILER
