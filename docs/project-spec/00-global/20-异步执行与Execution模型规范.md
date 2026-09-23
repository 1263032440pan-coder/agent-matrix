# 异步执行与 Execution 模型规范

## 1. 目的

Project Runtime 中任何可能持续超过普通 HTTP 请求生命周期的任务，必须采用 Execution-Oriented 模型。

核心原则：

```text
HTTP Request
!= Agent Execution
!= DB Transaction
!= Frontend Progress Stream
```

---

## 2. Task 与 Execution 的区别

### Task

Task 是项目计划中的工作单元。

Task FSM 保持：

```text
CREATED
READY
RUNNING
EXECUTION_DONE
REWORK
BLOCKED
FAILED
ACCEPTED
CANCELLED
```

### Execution

Execution 表示：

```text
一次具体执行尝试
```

一个 Task 可以对应多个 Execution：

```text
Task T001
├── Execution #1 → FAILED
├── Execution #2 → FAILED
└── Execution #3 → SUCCEEDED
```

因此：

```text
Task != Execution
```

---

## 3. Execution 状态

V1.13 最小状态：

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

可扩展：

```text
PAUSED
INTERRUPTED
RECOVERING
```

但不属于本阶段强制实现范围。

---

## 4. Execution 最小模型

```text
execution_id
project_id
task_id
attempt_no

status
phase
message

worker_id
skill_id
skill_version
skill_content_hash

created_at
started_at
updated_at
finished_at

error_code
error_message

correlation_id
```

---

## 5. API 规范

### 创建 Execution

```http
POST /api/executions
```

请求：

```json
{
  "project_id": "P001",
  "task_id": "T001"
}
```

成功：

```http
202 Accepted
```

返回：

```json
{
  "execution_id": "EXEC-001",
  "task_id": "T001",
  "status": "QUEUED"
}
```

### 查询 Execution

```http
GET /api/executions/{execution_id}
```

示例：

```json
{
  "execution_id": "EXEC-001",
  "task_id": "T001",
  "status": "RUNNING",
  "phase": "WORKER_EXECUTION",
  "message": "Running backend tests",
  "updated_at": "..."
}
```

---

## 6. 执行链

```text
Client
↓
POST /executions
↓
Execution Service
↓
Create QUEUED Execution
↓
Commit
↓
202 Accepted
↓
Scheduler / Background Runner
↓
Worker Runtime
↓
Execution Result
↓
Core Validation
↓
State Mutation
↓
Gate
```

---

## 7. HTTP 生命周期

禁止：

```text
HTTP Request
→ Worker
→ LLM
→ Shell
→ Review
→ Gate
→ HTTP Response
```

要求：

```text
HTTP Request
→ Create Execution
→ Commit
→ 202
```

后续执行必须独立。

---

## 8. Execution 与 Task 状态映射

推荐：

```text
Execution QUEUED
→ Task 保持 READY

Execution RUNNING
→ Task READY → RUNNING

Execution SUCCEEDED
→ Task RUNNING → EXECUTION_DONE

Gate PASS
→ Task EXECUTION_DONE → ACCEPTED

Gate FAIL
→ Task EXECUTION_DONE → REWORK
```

Execution 成功不等于 Task Accepted：

```text
EXECUTION SUCCEEDED
!=
TASK ACCEPTED
```

---

## 9. 幂等

重复调用执行接口时，Runtime 必须避免同一 Task 被无意创建多个活跃 Execution。

推荐约束：

```text
同一 Task 同一时刻最多一个 active Execution
```

active：

```text
QUEUED
RUNNING
```

需要显式 Retry 时再创建新的 attempt。

---

## 10. 禁止行为

禁止：

- Worker 直接设置 Task ACCEPTED；
- HTTP Request 长期等待 Worker 完成；
- 一个 Execution 与多个 Task 混用同一 Execution ID；
- 重试时覆盖旧 Execution；
- Execution 结束后删除历史执行记录；
- 把前端连接状态当成 Execution 状态。
