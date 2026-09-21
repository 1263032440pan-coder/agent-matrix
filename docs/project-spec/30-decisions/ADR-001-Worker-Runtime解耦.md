# ADR-001 Worker-Runtime解耦

## 状态

```text
ACCEPTED
```

## 决策

Project Runtime 只依赖 WorkerRuntimeAdapter，不直接依赖具体 Agent Runtime。

## 原因

保护 Project Runtime 的长期独立性，并允许 goose、Hermes、OpenCode、OpenHands 等替换。

## 约束

本 ADR 发生变更时必须创建新的架构变更记录，不允许由 Coding Agent 在实现过程中静默修改。
