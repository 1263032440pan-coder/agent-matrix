# ADR-035 Skill Resolution 采用 Rule-first + LLM-assisted

## 状态

ACCEPTED

## 决策

Skill Resolution 固定先经过确定性规则：

```text
Explicit Binding
>
Explicit Reference / Preferred Skill
>
Exact Metadata Match
>
Generic Fallback
```

只有多个候选在全部硬约束和优先级上完全等价时，才允许 LLM tie-break。

## 硬约束

LLM 不得绕过：

```text
Status
Version
Dependency
Capability
Worker Compatibility
Project Binding
```
