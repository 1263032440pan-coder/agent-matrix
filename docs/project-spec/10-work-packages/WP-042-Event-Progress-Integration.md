# WP-042 Event Progress Integration

## Goal

把 WP-011 Event Engine 正式接入 Execution、Worker、Skill、Artifact、Review、Gate 全链路。

---

## Scope

接入事件：

- Execution；
- Skill Resolver/Loader；
- Worker Runtime；
- Tool；
- Artifact；
- Reviewer/Repair；
- Gate。

实现：

```text
Event Engine
→ Event Store
→ Progress API
```

并至少提供一种实时推送：

```text
SSE
```

---

## UI

前端至少展示：

```text
当前状态
当前 phase
当前 message
执行时间线
```

示例：

```text
10:01 Execution queued
10:01 Skill resolved: software-developer@1.0.0
10:02 Worker started
10:03 Running tests
10:04 Tests passed
10:05 Review started
10:06 Review passed
10:06 Gate passed
```

---

## Out of Scope

- 完整分布式消息总线；
- Kafka；
- 跨节点 Event Replication；
- 高级 Trace UI。

---

## Acceptance Criteria

- Event 顺序可追踪；
- Event 有 execution_id；
- 页面刷新后可恢复历史；
- SSE 断开不影响 Worker；
- Worker 完成不依赖前端连接；
- 关键事件持久化；
- Event 写入不产生长事务。
