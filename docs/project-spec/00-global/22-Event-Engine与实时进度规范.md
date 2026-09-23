# Event Engine 与实时进度规范

## 1. 目的

实时进度不能依赖原始 `/execute` HTTP Response。

Project Runtime 应通过：

```text
Execution
+
Event Engine
```

表达长任务过程。

---

## 2. 进度不是百分比

Agent 执行流程通常不可准确映射为：

```text
37%
62%
```

因此 V1.13 采用语义阶段 + Event。

示例：

```text
QUEUED
正在解析 Skill
已加载 Skill
Worker 执行中
正在运行测试
Review 中
Gate 中
完成
```

---

## 3. 事件建议

最小事件：

```text
EXECUTION_QUEUED
EXECUTION_STARTED

SKILL_RESOLUTION_STARTED
SKILL_RESOLVED
SKILL_LOADED

WORKER_STARTED
WORKER_COMPLETED

TOOL_STARTED
TOOL_COMPLETED

ARTIFACT_CREATED

REVIEW_STARTED
REVIEW_FAILED
REVIEW_PASSED

REPAIR_STARTED
REPAIR_COMPLETED

GATE_STARTED
GATE_FAILED
GATE_PASSED

EXECUTION_COMPLETED
EXECUTION_FAILED
EXECUTION_CANCELLED
```

---

## 4. Event 最小字段

```text
event_id
event_type
timestamp

project_id
task_id
execution_id

phase
message

actor
correlation_id
causation_id

payload
```

---

## 5. State 与 Event

两者必须同时存在。

### State

回答：

```text
现在是什么状态？
```

例如：

```text
Execution.status = RUNNING
Execution.phase = REVIEW
```

### Event

回答：

```text
之前发生过什么？
```

因此：

```text
State + Event
```

而不是二选一。

---

## 6. 第一阶段 UI

在 Event Engine 全量接入前允许：

```text
GET /executions/{id}
```

前端 Polling：

```text
1～2 秒一次
```

用于 V1.13 P0/P1。

---

## 7. 第二阶段 UI

WP-011 接入后：

```text
Event Engine
→ Event Store
→ SSE / WebSocket
→ Frontend Timeline
```

推荐优先 SSE：

```text
单向服务端推送
结构简单
适合进度事件
```

若未来需要双向控制，再考虑 WebSocket。

---

## 8. 断线恢复

前端断线或刷新后：

```text
execution_id
```

必须仍可查询。

Event Stream 推荐支持：

```text
after_event_id
```

或：

```text
last_event_sequence
```

恢复历史事件后继续接收新事件。

---

## 9. Event 持久化

关键 Runtime Event 应写入 Event Store。

Event 写入必须使用短事务。

不得：

- Event 仅存在前端内存；
- 页面关闭后全部进度丢失；
- Worker 完成后无法追溯；
- 使用 UI 状态替代正式 Execution/Event 状态。
