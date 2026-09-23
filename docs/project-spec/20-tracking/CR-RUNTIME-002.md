# CR-RUNTIME-002 Async Execution & Real-time Progress

## Problem

长 Agent Task 与 HTTP Request 生命周期耦合。

后果：

- 请求长期占用；
- 前端刷新后难以恢复；
- 实时进度能力弱；
- 容易与 DB Session 生命周期绑定；
- 不利于 Retry / Recovery。

## Decision

采用：

```text
POST
→ 202 + execution_id
→ Background Execution
```

第一阶段：

```text
GET /executions/{id}
→ Polling
```

第二阶段：

```text
WP-011 Event Engine
→ Event Store
→ SSE
```

## Related

- WP-040
- WP-042
- ADR-041（原 038 顺延）
- ADR-043（原 040 顺延）
