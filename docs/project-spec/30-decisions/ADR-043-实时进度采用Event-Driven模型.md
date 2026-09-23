# ADR-043 实时进度采用 Event-Driven 模型

## Status

ACCEPTED

## Decision

实时进度采用：

```text
Execution State
+
Runtime Event
```

而不是仅依赖：

```text
progress_percent
```

第一阶段允许 Polling。

正式实时链：

```text
WP-011 Event Engine
→ Event Store
→ SSE
→ Frontend Timeline
```

## Rationale

Agent 流程不可准确预测百分比。

用户真正需要知道的是：

```text
当前阶段
正在执行什么
之前发生了什么
是否失败/返修
是否通过 Gate
```

## Consequences

Event 需要持久化并关联 execution_id。
