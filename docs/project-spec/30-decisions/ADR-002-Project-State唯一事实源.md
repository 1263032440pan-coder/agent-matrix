# ADR-002 Project-State唯一事实源

## 状态

```text
ACCEPTED
```

## 决策

项目级真实状态统一持久化到 Project State。

## 原因

Worker Session 和 LLM Context 都可能丢失，不能作为项目事实源。

## 约束

本 ADR 发生变更时必须创建新的架构变更记录，不允许由 Coding Agent 在实现过程中静默修改。
