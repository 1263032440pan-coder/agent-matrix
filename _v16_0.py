"""V1.6 序0: file_rw 任务级禁闭 + WorkPackage.workspace_root。

方案（设计单 V1.6 §三序0 / 原四缺陷单缺陷1）:
- WorkPackage 加 workspace_root（service 构造时传 data/tasks/{pid}/{tid}）
- _run_tool_plugin 分发 tool:file_rw 时 payload 注入 __ws_root
- FileRwProvider.invoke write 分支: 有 __ws_root → resolved 必须落其内,
  越界返回指引文案; 无 → data/tasks 原语义（测试/独立调用兼容）
- read/list 维持 data/tasks 全域
"""

# ── ① adapter.py: WorkPackage.workspace_root ──
p = 'project_runtime/worker/adapter.py'
s = open(p, encoding='utf-8').read()
old = '''    # V1.5a-1: 代码交付阶段标记（skill stages/材料兜底透传——软提醒消费）
    code_stage: bool = False'''
new = '''    # V1.5a-1: 代码交付阶段标记（skill stages/材料兜底透传——软提醒消费）
    code_stage: bool = False
    # V1.6 序0: 任务工作区根（file_rw 写禁闭——安全机制）
    workspace_root: str | None = None'''
assert old in s, 'adapter anchor'
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print('adapter ok')

# ── ② service.py: 构造 WorkPackage 时传 workspace_root ──
p = 'project_runtime/execution/service.py'
s = open(p, encoding='utf-8').read()
old = '''                    # V1.5a-1: code_stage 透传（playbook_ref 解析——软提醒用）
                    code_stage=_code_stage_of(task_playbook_ref_snapshot),'''
new = '''                    # V1.5a-1: code_stage 透传（playbook_ref 解析——软提醒用）
                    code_stage=_code_stage_of(task_playbook_ref_snapshot),
                    # V1.6 序0: 任务工作区根（file_rw 写禁闭）
                    workspace_root=f"data/tasks/{project_id}/{task_id}",'''
assert old in s, 'service anchor'
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print('service ok')

# ── ③ llm_worker.py: file_rw 分发注入 __ws_root ──
p = 'project_runtime/worker/llm_worker.py'
s = open(p, encoding='utf-8').read()
old = '''        for info in shared_registry().list_providers(CapabilityType.TOOL):
            if info.name == name and info.enabled:
                try:
                    return await info.provider.invoke(args)
                except Exception as e:  # noqa: BLE001 — 降级不炸循环
                    return {"ok": False, "error": str(e)[:300]}
        return None'''
new = '''        for info in shared_registry().list_providers(CapabilityType.TOOL):
            if info.name == name and info.enabled:
                try:
                    payload = dict(args)
                    # V1.6 序0: file_rw 写禁闭——注入任务工作区根
                    # （__ 前缀=内部参数, schema 不暴露, Provider 侧剥离）
                    if name == "tool:file_rw" and self._workspace_root:
                        payload["__ws_root"] = self._workspace_root
                    return await info.provider.invoke(payload)
                except Exception as e:  # noqa: BLE001 — 降级不炸循环
                    return {"ok": False, "error": str(e)[:300]}
        return None'''
assert old in s, 'worker dispatch anchor'
s = s.replace(old, new)
# assign 时记录 workspace_root（Phase1 前与 _current_task_id 同步）
old2 = '''            self._current_task_id = task.id
            await self._fsm.transition_task(task, TaskStatus.RUNNING)'''
new2 = '''            self._current_task_id = task.id
            self._workspace_root = getattr(wp, "workspace_root", None)
            await self._fsm.transition_task(task, TaskStatus.RUNNING)'''
assert old2 in s, 'assign anchor'
s = s.replace(old2, new2)
# __init__ 初始化
old3 = '''        # V1.5a-1 序6/7: 工作区快照/软提醒状态
        self._current_task_id: int | None = None
        self._nudged_tasks: set[int] = set()'''
new3 = '''        # V1.5a-1 序6/7: 工作区快照/软提醒状态
        self._current_task_id: int | None = None
        self._nudged_tasks: set[int] = set()
        # V1.6 序0: 任务工作区根（file_rw 写禁闭）
        self._workspace_root: str | None = None'''
assert old3 in s, 'init anchor'
s = s.replace(old3, new3)
open(p, 'w', encoding='utf-8').write(s)
print('llm_worker ok')

# ── ④ tools.py: FileRwProvider write 分支任务级禁闭 ──
p = 'project_runtime/worker/tools.py'
s = open(p, encoding='utf-8').read()
old = '''        if action == "write":
            content = payload.get("content")
            if content is None or not str(content).strip():
                return {"ok": False, "error": "write 需要 content"}
            content = str(content)
            try:
                resolved = self._resolve(path)'''
new = '''        if action == "write":
            content = payload.get("content")
            if content is None or not str(content).strip():
                return {"ok": False, "error": "write 需要 content"}
            content = str(content)
            # V1.6 序0: 任务级写禁闭——调用方注入 __ws_root 时,
            # resolved 必须落在该任务工作区内（防写到相邻目录逃逸 harvest）
            ws_root_raw = payload.get("__ws_root")
            try:
                if ws_root_raw:
                    ws_root = Path(str(ws_root_raw)).resolve()
                    resolved = (Path(path) if Path(path).is_absolute()
                                else Path.cwd() / path).resolve()
                    if not str(resolved).startswith(str(ws_root)):
                        return {
                            "ok": False,
                            "error": (
                                f"路径越界: {path}——本任务只允许写入 "
                                f"{ws_root} 内。请用相对路径如 "
                                f"src/main.py、tests/test_x.py、README.md"
                            ),
                        }
                else:
                    resolved = self._resolve(path)'''
assert old in s, 'tools write anchor'
s = s.replace(old, new)
open(p, 'w', encoding='utf-8').write(s)
print('tools ok')
