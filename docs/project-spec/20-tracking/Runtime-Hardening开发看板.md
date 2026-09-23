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
PASS（待检查方复验确认）
```

Gate 勾选（依据 tests/test_concurrency_hardening.py 3 用例 + 全量回归）：

- [x] session isolation（ExecutionRunner 每阶段独立 AsyncSession；测试 fixture 直传 async engine）
- [x] no long transaction（LLMWorker/MockWorker Phase 1 改 commit —— 此前 flush 导致 LLM 全程持写事务，实测并发写被挡 5.2s → 修复后 DB-04 用例实证 LLM 飞行期外部写毫秒级成功）
- [x] short mutation transaction（commit_with_retry：3 次上限 + 指数退避 + 审计事件，DB-06 用例覆盖）
- [x] concurrent execution test（3 Execution 并发全 SUCCEEDED，无持续 locked，耗时 <1s）
- [x] database locked 显著消除（busy_timeout 30s 保留为防御，正常路径不依赖——DB-07 断言）

连带修复（commit 后 ORM expire 系统性适配）：Scheduler 快照 automation_mode/task id/name；test_llm_worker/test_worker/test_scheduler/test_skill_loader/test_e2e/scripts/e2e_demo.py 全部改 scalar select 读法。

---

## RH-03 — Event Progress Integration

对应：

```text
WP-042
```

状态：

```text
PASS（待检查方复验确认）
```

Gate 勾选（依据 tests/test_event_engine.py 5 用例 + 真实 LLM 实测 execution #4 事件链 + SSE 流实测）：

- [x] Event Store（runtime_event 表，spec §4 全字段 + 21 事件类型；migration c3d7e8f4a9b1 手写 DDL）
- [x] Execution timeline（ExecutionRunner 全链埋点：QUEUED→STARTED→WORKER_STARTED/COMPLETED→ARTIFACT_CREATED→COMPLETED/FAILED；GateCore GATE_STARTED/PASSED/FAILED）
- [x] SSE（`GET /api/executions/{id}/events/stream`，实测标准 id/event/data 帧 + keep-alive 心跳；订阅者断开不影响 Worker——EventEngine 纯持久化，推送独立拉取）
- [x] refresh/reconnect recovery（事件持久化 + after_id 增量恢复；test_after_id_reconnection_recovery + events API 实测）
- [x] Event 顺序可追踪（id 严格递增；全事件携带 execution_id）
- [x] Event 写入不产生长事务（每事件独立 session + commit_with_retry；test_event_write_is_short_tx spy 实证）
- [x] 事件失败不破坏执行（_emit best-effort 双层吞异常；test_event_failure_never_breaks_execution 实证事件存储挂掉执行仍 SUCCEEDED）
- [x] WP-011 接入（Event Engine 落地为 WP-042 范围的最小实现；完整 Event Engine 事件总线按 ADR-041 阶段划分不提前做）

衔接：`GET /api/executions/diagnostics/locks` 消费 get_lock_audit（RH-02 复验要求）；UI Dashboard 新增「Runtime Events」实时进度区块。

技术备注：SQLite BigInteger 主键破坏 autoincrement（BIGINT 不做 rowid 别名）→ runtime_event.id 用 Integer。

---

## 完成标准

```text
RH-01 PASS
AND RH-02 PASS
AND RH-03 PASS
→ Runtime Hardening PASS
```
