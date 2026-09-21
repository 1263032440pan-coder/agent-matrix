# 15 Pipeline Engine 与 Hook 规范

## Base Pipeline

例如 Task Execution：

```yaml
pipeline: task_execution
steps:
  - task.prepare
  - automation.check
  - context.build
  - worker.select
  - worker.execute
  - result.process
  - gate.evaluate
```

## 编译

```text
Base Pipeline
+ Hook
+ Middleware
+ Validator
=
Compiled Pipeline
```

## AOP 对应关系

```text
before.*   ≈ Before Advice
middleware ≈ Around / Data Pipeline
core step  ≈ Join Point
after.*    ≈ After Advice
validator  ≈ Post-condition
```

## Hook Result

```text
CONTINUE
PAUSE
RETRY
NEED_USER_INPUT
PROPOSAL
```

## Fail Policy

每个 Extension Point Contract 定义：

```text
timeout
fail_open / fail_closed
max_retry
required / optional
```

例如：

```text
event.observers
→ fail_open

gate.validators / mandatory security
→ 可配置 fail_closed
```

## 事务边界

插件调用发生在 Core Transaction 外部。

插件返回结果后：

```text
Core Validate
→ Begin Transaction
→ Apply State Change
→ Commit
→ Emit Event
```

避免插件崩溃造成数据库半写状态。


## Automation Mode Boundary

Runtime Extension 只能提出 `PAUSE / RETRY / BLOCK / NEED_USER_INPUT / PROPOSAL` 等结果。
插件不能把 `FULL_AUTO` 改造成“遇风险就询问”的第三种模式；MANUAL 是否等待人工由 Core Automation Controller 决定。
