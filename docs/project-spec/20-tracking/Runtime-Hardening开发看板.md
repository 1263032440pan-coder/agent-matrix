# Runtime Hardening 开发看板

## Baseline

```text
D01～D18 = PASS / FROZEN
```

---

## RH-01 — Async Execution

对应：

```text
WP-040
```

状态：

```text
PASS（待检查方复验确认）
```

Gate 勾选（依据 tests/test_execution.py 7 用例 + 返修单-RH01 整改）：

- [x] 202 Accepted（POST /api/executions 秒回，test_submit_returns_immediately 实测 <0.2s）
- [x] execution_id（响应含 id，持久化可查）
- [x] background execution（asyncio.create_task 后台 runner，HTTP 返回后仍完成）
- [x] Task/Execution 分离（Execution 独立表 + FSM；Task 仅 EXECUTION_DONE，ACCEPTED 归 Gate — AE-07 断言）
- [x] retry attempt（attempt_no 递增，历史保留不覆盖 — AE-06）
- [x] 页面刷新可恢复（DB 持久化，新 session 可查 — AE-04 实测）
- [x] AE-05 并发防重（部分唯一索引 ux_execution_task_active_one DB 层兜底，asyncio.gather 双并发实测恰好一个成功）

旧端点处置（前置 2 方案 A）：/api/projects/{id}/execute 改 202 薄封装，MANUAL 模式仍走 ManualAction 红线保持。

---

## RH-02 — SQLite Concurrency Hardening

对应：

```text
WP-041
```

状态：

```text
NOT_STARTED
```

Gate：

- session isolation；
- no long transaction；
- short mutation transaction；
- concurrent execution test；
- database locked 显著消除。

---

## RH-03 — Event Progress Integration

对应：

```text
WP-042
```

状态：

```text
NOT_STARTED
```

Gate：

- WP-011 接入；
- Event Store；
- Execution timeline；
- SSE；
- refresh/reconnect recovery。

---

## 完成标准

```text
RH-01 PASS
AND RH-02 PASS
AND RH-03 PASS
→ Runtime Hardening PASS
```
