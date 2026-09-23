# ADR-042 数据库采用短事务与 Session 隔离

## Status

ACCEPTED

## Decision

继续使用 SQLite，但规定：

```text
One Execution
→ Independent Session
```

以及：

```text
One State Mutation
→ Short Transaction
```

外部长任务期间：

```text
No Open DB Transaction
```

## Rationale

当前主要场景仍是本地桌面、单用户，SQLite 合适。

锁竞争根因优先通过正确事务模型处理，而不是立即迁移数据库。

## Consequences

- 禁止后台复用 Request Session；
- Worker 不长期持有 ORM Entity；
- WAL / busy_timeout 保留为保护措施；
- 未来服务端多用户再评估 PostgreSQL。
