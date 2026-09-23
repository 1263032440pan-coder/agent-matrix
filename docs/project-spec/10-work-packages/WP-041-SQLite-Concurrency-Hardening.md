# WP-041 SQLite Concurrency Hardening

## Goal

根治当前主要由长事务、共享 Session 与并发写模型造成的 SQLite 锁竞争。

---

## Scope

实现：

- Session per Execution；
- Session per transaction；
- 禁止 Request Session 进入后台；
- 外部 Worker/LLM/Tool 执行期间无 DB transaction；
- Core 状态短事务；
- SQLite busy 有限重试；
- lock wait metrics / audit；
- 并发回归测试。

---

## 必须检查的代码

重点搜索：

```text
Session()
session.begin()
Depends(get_db)
background task
scheduler callback
worker callback
execute()
commit()
flush()
```

确认不存在：

```text
Request Session
→ Background Task
```

以及：

```text
BEGIN
→ Long Agent Work
→ COMMIT
```

---

## Acceptance Criteria

- 每个后台 Execution 独立 Session；
- Worker 执行 60s 时数据库无长事务；
- 并发 3 个 Execution 可正常运行；
- 不频繁触发 `database is locked`；
- WAL + busy_timeout 保留；
- 移除对“30 秒等待锁”的依赖；
- 所有 Task/Project 状态仍由 Core Mutation 层修改。
