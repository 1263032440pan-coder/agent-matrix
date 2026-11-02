# Orphan Recovery 方案 B（判死停住）

## 问题
Runtime 重启后 `_orphan_recovery` 对 RUNNING execution 的 FAILED 写入落在 detached ORM 对象上，commit 空事务；task / agent 未清场，UI 长期显示 RUNNING。

## 决策
方案 B：RUNNING orphan 判死停住，不自动重提。

## 行为
- `QUEUED`：保留状态，返回 id 列表由 lifespan 重新挂 `ExecutionRunner`
- `RUNNING`（同一 session）：
  - execution → FAILED（`ORPHAN_RECOVERY`）
  - task 若仍 RUNNING → FSM → FAILED
  - 占用该 task 的 Agent：BUSY→IDLE，清 `current_task_id`
- 不自动 FAILED→READY

## 实现
- `project_runtime/execution/orphan_recovery.py::recover_orphan_executions`
- `apps/runtime/main.py` lifespan 调用
- 测试：`tests/test_orphan_recovery.py`
