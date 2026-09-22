# ADR-036 Skill 不拥有 Project State

## 状态

ACCEPTED

## 决策

任何 Skill 均不得直接修改：

```text
Project State
Task State
Task Graph
Event Bus
```

Skill 只能形成方法上下文、Worker Result、Artifact、Evidence 或 Proposal。

真正状态变化继续遵守：

```text
Command / Proposal
→ StateTransitionService
→ Validation
→ Transaction
→ State Mutation
→ Event
```
