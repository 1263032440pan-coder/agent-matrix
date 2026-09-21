# ADR-021 插件不得直接修改 Project State

## 状态

ACCEPTED

## 决策

任何插件都不得拥有 Project State 直接写权限。

状态变化：

```text
Extension
→ Proposal / Command
→ Core Validation
→ Transaction
→ State Mutation
```

## 原因

保持 Project Runtime 为唯一事实源，避免插件导致不可审计状态漂移。
