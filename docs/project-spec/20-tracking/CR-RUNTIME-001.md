# CR-RUNTIME-001 SQLite Write Contention

## Problem

真实运行中出现 SQLite 锁竞争。

已使用：

```text
WAL
busy_timeout = 30s
```

问题缓解但未根治。

## Root Cause

优先怀疑：

- 长事务；
- 多 Worker 共享 Session；
- Request Session 传给后台任务；
- Worker/LLM/Tool 期间持有 transaction；
- 多组件直接并发写 SQLite。

## Decision

采用：

```text
Session Isolation
+
Short Transaction
+
Centralized State Mutation
```

不立即更换 PostgreSQL。

## Related

- WP-041
- ADR-042（原补丁编号 039，因 038 已被幂等策略占用而顺延）
- Runtime Hardening测试矩阵
