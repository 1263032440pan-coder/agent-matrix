# WP-035 Pipeline Engine 与 Extension Points

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

把核心生命周期拆成稳定 Base Pipeline，并将 Extension Point 编译为可执行 Pipeline。

## 前置依赖

```text
WP-033 Capability Extension Framework
WP-034 Runtime Extension Framework
```

## 首批 Pipeline

```text
project_bootstrap
project_understanding
planning
task_execution
gate_evaluation
delivery_validation
```

## Task Execution

```text
task.prepare
→ automation.check
→ context.build
→ worker.select
→ worker.execute
→ result.process
→ gate.evaluate
```

## Extension 顺序

支持：

```text
priority
before
after
```

必须构建 Extension Dependency Graph 并拓扑排序。

## 冲突

```text
EXTENSION_DEPENDENCY_CYCLE
```

必须阻止编译。

## 事务原则

插件调用在 State Transaction 外执行。

## 验收

- [ ] Base Pipeline 可声明。
- [ ] Hook / Middleware / Validator 可注入。
- [ ] 编译结果可查看。
- [ ] 插件 enable/disable 会触发重新 compile。
- [ ] 顺序冲突可检测。
