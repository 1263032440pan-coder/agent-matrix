# SQLite 并发与事务规范

## 1. 背景

当前运行环境继续使用：

```text
SQLite
+ WAL
+ busy_timeout
```

WAL 与 busy_timeout 只能缓解锁竞争，不解决长事务和并发写模型问题。

---

## 2. 核心原则

```text
Long-running Work
不得持有 DB Transaction
```

```text
One Execution
→ Independent Session
```

```text
One State Mutation
→ Short Transaction
```

---

## 3. Session 生命周期

禁止：

```text
创建 Session
↓
调用 Worker
↓
调用 LLM
↓
运行 Shell
↓
等待几十秒/几分钟
↓
commit
```

要求：

```text
Session A
BEGIN
写 RUNNING
COMMIT
CLOSE

执行 LLM / Worker / Tool

Session B
BEGIN
写 Result
COMMIT
CLOSE
```

---

## 4. 独立 Session

每个后台 Execution 必须使用独立 Session。

禁止：

- 多个后台 Task 共享 SQLAlchemy Session；
- HTTP Request Session 传递给后台 Worker；
- Worker 线程/协程跨上下文复用 ORM Session；
- 长时间缓存带 Session 的 ORM Entity。

---

## 5. State Mutation

所有核心状态变化仍遵守：

```text
Worker / Extension
→ Result / Proposal
→ Core Validation
→ StateTransitionService
→ Short Transaction
→ State Mutation
→ Event
```

Worker 不得长期直接操作 SQLite。

---

## 6. SQLite 写入目标

每次数据库写入应尽可能：

```text
毫秒级完成
```

典型链：

```text
BEGIN
UPDATE execution
INSERT event
COMMIT
```

然后立即释放事务。

---

## 7. WAL / busy_timeout

保留：

```text
journal_mode = WAL
busy_timeout = 30000
```

但这只是防御性配置。

不允许把：

```text
busy_timeout = 30s
```

作为系统正确运行的必要条件。

如果正常路径经常等待数秒锁，则必须判断为：

```text
Concurrency Design Defect
```

---

## 8. 重试

针对瞬时：

```text
SQLITE_BUSY
database is locked
```

允许有限重试。

要求：

- 有最大重试次数；
- 有退避；
- 有审计；
- 不允许无限重试；
- 不允许用重试掩盖长事务。

---

## 9. SQLite 适用范围

当前适用：

```text
桌面应用
本地单用户
低至中等并发
少量 Worker
```

未来出现以下情况时应重新评估 PostgreSQL：

```text
多用户
服务端部署
大量并发 Worker
大量持续写入 Event
跨进程高并发
```

数据库迁移不属于 V1.13 强制范围。

---

## 10. 验收标准

必须验证：

- 长 Worker 运行期间没有开放写事务；
- 多 Execution 不共享 Session；
- 页面刷新不影响 Execution；
- 并发执行不再频繁触发 database is locked；
- busy_timeout 不再成为主要“稳定手段”。
