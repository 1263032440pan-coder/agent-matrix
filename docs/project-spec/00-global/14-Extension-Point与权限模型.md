# 14 Extension Point 与权限模型

## 稳定 Extension Point

```text
project.bootstrap.before
project.bootstrap.after
project.understanding.before
project.understanding.after
capability.assessment.after
research.providers
playbook.build.before
playbook.build.after
planner.before
planner.after
scheduler.before_dispatch
context.providers
context.middleware
worker.before_execute
worker.after_execute
artifact.processors
retrieval.providers
gate.validators
delivery.validators
policy.extensions
event.observers
```

## 权限等级

```text
READ_ONLY
ENRICH
VALIDATE
CONTROL
```

## 权限约束

### READ_ONLY

允许读取 Scope 内数据、订阅 Event。

### ENRICH

允许返回：

```text
Context Enrichment
Metadata
Research Result
Scan Result
```

### VALIDATE

允许返回：

```text
PASS
WARNING
FAIL
```

### CONTROL

允许提出：

```text
PAUSE
RETRY
NEED_USER_INPUT
DENY_ACTION
```

Core 最终决定。

## 明确禁止

```text
DIRECT_STATE_MUTATE
DIRECT_EVENTBUS_MUTATE
DIRECT_TASKGRAPH_MUTATE
```

## 顺序

```text
priority
before
after
```

用于构建 Extension Dependency Graph。

依赖环：

```text
EXTENSION_DEPENDENCY_CYCLE
```

冲突插件不得启用。


## Automation Mode Boundary

Runtime Extension 只能提出 `PAUSE / RETRY / BLOCK / NEED_USER_INPUT / PROPOSAL` 等结果。
插件不能把 `FULL_AUTO` 改造成“遇风险就询问”的第三种模式；MANUAL 是否等待人工由 Core Automation Controller 决定。

## V1.9 Method Intelligence Extension Points

```text
method.assets.providers
method.assets.processors
procedure.normalizers
method.readiness.evaluators
gap.classifiers
gap.resolvers
playbook.provenance.validators
```

这些点允许以后更换项目方法识别/评估策略，而不修改 Stable Kernel。
