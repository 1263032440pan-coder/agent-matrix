# WP-034 Runtime Extension Framework

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] Observer 无阻断能力。
- [ ] Validator 无直接状态写权限。
- [ ] Middleware 返回新对象。
- [ ] Hook 可提出 Control Result。
- [ ] 权限不匹配时 Extension 调用被拒绝。


## Automation Mode Boundary

Runtime Extension 不得提出“遇风险就转人工”的第三种模式。

插件返回 `NEED_USER_INPUT` 时，仅说明缺必要输入；是否存在人工推进等待由 Automation Mode Controller 决定。
