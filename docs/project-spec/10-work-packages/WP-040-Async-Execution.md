# WP-040 Async Execution

## Goal

将现有同步 Execute 模型改造为 Execution-Oriented 异步模型。

---

## Scope

实现：

- Execution Domain Model；
- Execution Repository；
- `POST /api/executions`；
- `GET /api/executions/{id}`；
- 202 Accepted；
- background execution；
- attempt_no；
- active execution 防重复；
- Execution 与 Task FSM 的正确映射。

---

## Out of Scope

本 WP 不要求：

- SSE；
- WebSocket；
- 完整 Event Store；
- Crash Recovery；
- Distributed Queue；
- PostgreSQL。

---

## Implementation Rules

```text
POST
→ Create QUEUED Execution
→ Commit
→ Return 202
```

后台：

```text
QUEUED
→ RUNNING
→ Worker
→ SUCCEEDED / FAILED
```

Task：

```text
READY
→ RUNNING
→ EXECUTION_DONE
```

Gate 独立完成：

```text
EXECUTION_DONE
→ ACCEPTED / REWORK
```

---

## Acceptance Criteria

- HTTP 请求不等待完整 Worker 执行；
- 立即获得 execution_id；
- 页面刷新后 execution_id 仍有效；
- 同一 Task 不会意外产生两个 active Execution；
- Failed Retry 产生新的 attempt；
- Execution SUCCEEDED 不会直接 ACCEPT Task。
