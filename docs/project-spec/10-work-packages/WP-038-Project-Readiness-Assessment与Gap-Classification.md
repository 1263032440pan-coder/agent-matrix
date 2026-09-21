# WP-038 Project Readiness Assessment 与 Gap Classification

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

分别评估：

```text
Method Readiness
Execution Capability Readiness
```

并生成精确 Gap。

## 前置依赖

```text
WP-027 Skill Registry & Execution Capability Readiness
WP-037 Project Method Asset Discovery & Procedure Normalization
```

## Method Readiness 维度

```text
Scope Coverage
Action Explicitness
Artifact Definition
Acceptance Definition
Dependency / Transition
Constraint / Authority
```

## 节点状态

```text
READY
PARTIAL
AMBIGUOUS
CONFLICTED
MISSING
```

## Gap Type

```text
PRIVATE
EXPLICITNESS
DOMAIN
TECHNICAL
PROCESS
CAPABILITY
```

## Extension Points

```text
method.readiness.evaluators
gap.classifiers
gap.resolvers
```

## Gate

- [ ] 有大量文档不等于自动判 READY。
- [ ] 能识别人类可理解但 AI 不可执行的流程缺口。
- [ ] Method / Execution Readiness 可分别为 READY / MISSING。
- [ ] Skill=0 本身不触发 Research。
