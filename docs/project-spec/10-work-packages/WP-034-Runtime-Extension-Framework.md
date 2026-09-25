# WP-034 Runtime Extension Framework

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_runtime_extension.py 10 用例，含真实执行链接线与 advisory pause 实证）

## 目标

实现 Runtime Extension Plane：

```text
Observer
Validator
Middleware
Hook
Policy Extension
```

## 前置依赖

```text
WP-011 Event Engine
WP-013 Automation Mode Controller
WP-032 Plugin Manager 与 Manifest
```

## Permission

```text
READ_ONLY
ENRICH
VALIDATE
CONTROL
```

禁止：

```text
DIRECT_STATE_MUTATE
```

## 结果 Contract

Observer：

```text
ACK
```

Validator：

```text
PASS / WARNING / FAIL
```

Hook：

```text
CONTINUE / PAUSE / RETRY / NEED_USER_INPUT / PROPOSAL
```

## 状态修改

任何 Proposal 必须：

```text
Core Validation
→ Transaction
→ State Mutation
```

## 验收

- [x] Observer 无阻断能力（test_observer_cannot_block：恶意返回 PAUSE 的 Observer 被强制归一 ACK，control 恒 CONTINUE）
- [x] Validator 无直接状态写权限（test_validator_contract_only：只能 PASS/WARNING/FAIL；返回控制值 → EXTENSION_ERROR 且不影响 control）
- [x] Middleware 返回新对象（test_middleware_returns_new_object：返回新 payload，原对象零改动）
- [x] Hook 可提出 Control Result（test_hook_proposes_control：NEED_USER_INPUT 聚合优先于 CONTINUE；PROPOSAL 结果可收集）
- [x] 权限不匹配时 Extension 调用被拒绝（test_permission_mismatch_rejected：READ_ONLY 的 HOOK 在调用前被拒，impl.invoked==0；权限矩阵五类对应）

实现：project_runtime/extension/runtime/（contracts 五类结果契约 + KIND_PERMISSION 矩阵；executor 稳定点纪律 21 点 + fire_hook 聚合 + 失败隔离）
真实接线：ExecutionRunner fire worker.before_execute（advisory 控制：非 CONTINUE 本轮不派发，Core 决定）/ worker.after_execute（通知型）
Automation Mode 边界：NEED_USER_INPUT 仅表示缺输入，是否等人工由 Mode Controller 决定（未引入第三模式）
Proposal 路径：结果仅收集——状态变更必须走既有 Core Validation → Transaction（FSM actor 防护即执法点）


## Automation Mode Boundary

Runtime Extension 不得提出“遇风险就转人工”的第三种模式。

插件返回 `NEED_USER_INPUT` 时，仅说明缺必要输入；是否存在人工推进等待由 Automation Mode Controller 决定。
