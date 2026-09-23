# Runtime Hardening 测试矩阵

## A. Async Execution

| ID | Test | Expected |
|---|---|---|
| AE-01 | 提交长任务 | 短时间返回 202 |
| AE-02 | 响应包含 execution_id | PASS |
| AE-03 | HTTP 返回后 Worker 继续运行 | PASS |
| AE-04 | 页面刷新后查询 execution_id | 状态仍存在 |
| AE-05 | 同 Task 重复启动 | 不产生两个 active Execution |
| AE-06 | Execution failed 后 retry | 创建 attempt+1 |
| AE-07 | Execution SUCCEEDED | Task 仅到 EXECUTION_DONE |
| AE-08 | Gate PASS | Task 才 ACCEPTED |

## B. Session / Transaction

| ID | Test | Expected |
|---|---|---|
| DB-01 | Worker 执行 60s | 无长写事务 |
| DB-02 | 3 Execution 并发 | Session 独立 |
| DB-03 | Request 结束 | 后台不使用 Request Session |
| DB-04 | LLM 30s | DB transaction 已释放 |
| DB-05 | Shell 30s | DB transaction 已释放 |
| DB-06 | database is locked | 有限重试 + 审计 |
| DB-07 | 正常并发 | 不依赖 30s busy timeout 才成功 |

## C. Event Engine

| ID | Test | Expected |
|---|---|---|
| EV-01 | Execution start | EXECUTION_STARTED |
| EV-02 | Skill resolve | SKILL_RESOLVED |
| EV-03 | Worker start/end | Event 完整 |
| EV-04 | Tool run | TOOL_STARTED/COMPLETED |
| EV-05 | Artifact | ARTIFACT_CREATED |
| EV-06 | Review FAIL | REVIEW_FAILED |
| EV-07 | Repair | REPAIR_STARTED/COMPLETED |
| EV-08 | Gate PASS | GATE_PASSED |
| EV-09 | 页面刷新 | 历史 Event 可恢复 |
| EV-10 | SSE 断线 | Worker 不受影响 |

## D. SQLite Concurrency

### 场景

同时运行：

```text
Execution A
Execution B
Execution C
```

每个 Execution 至少产生：

```text
status update
skill event
worker event
artifact metadata
gate event
```

预期：

```text
无持续 database is locked
无共享 Session 错误
无 transaction 跨 Worker 泄漏
Event 顺序可追踪
Task 最终状态正确
```

## E. E2E

```text
POST /executions
→ 202
→ QUEUED
→ RUNNING
→ Skill Resolve
→ Worker
→ Artifact
→ Review
→ Gate
→ EXECUTION_DONE
→ ACCEPTED
```

前端全过程可通过：

```text
Polling 或 SSE
```

观察。

## F. Fail Gate

出现任一情况必须 FAIL：

- HTTP 仍需等待整个 Agent 执行；
- 后台任务复用 Request Session；
- Worker 执行期间长期持有事务；
- Execution SUCCEEDED 直接把 Task ACCEPTED；
- 页面刷新后 Execution 消失；
- Event 仅存在内存不可追溯；
- 并发 3 个 Execution 稳定触发 database is locked。
