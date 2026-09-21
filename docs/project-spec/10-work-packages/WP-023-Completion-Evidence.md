# WP-023 Completion Evidence

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 必读

```text
00-global/03-核心领域模型.md
00-global/07-长项目上下文与项目记忆策略.md
30-decisions/ADR-010-完成判定基于证据链.md
```

## 1. 任务目标

实现 Task、模块、Delivery Item、Project 的结构化完成证据。

## 2. 前置依赖

```text
WP-014 Gate
WP-016 Delivery Contract
WP-022 Traceability Graph
```

## 3. Evidence 类型

```text
TASK_ACCEPTED
GATE_PASSED
TEST_PASSED
ARTIFACT_EXISTS
CONTRACT_ITEM_SATISFIED
MANUAL_ACTION_CONFIRMED
INTEGRATION_PASSED
MANUAL_EVIDENCE
```

## 4. 核心接口

```text
create_evidence()
invalidate_evidence()
validate_scope()
get_scope_evidence()
check_delivery_item()
check_project_completion()
```

## 5. Project Completion

```text
Required Delivery Item SATISFIED
AND
Traceability Coverage Complete
AND
Required Evidence Valid
AND
Required Task ACCEPTED
AND
Critical Issue = 0
AND
Final Gate PASS
AND
Pending ManualAction = 0
```

## 6. 验收条件

- [ ] Evidence 可关联 Task / Gate / Artifact / Test / Delivery Item。
- [ ] Evidence 可失效和重建。
- [ ] Delivery Item 不能在 Evidence 缺失时标记 SATISFIED。
- [ ] Project Completion 不依赖单次 LLM 判断。
