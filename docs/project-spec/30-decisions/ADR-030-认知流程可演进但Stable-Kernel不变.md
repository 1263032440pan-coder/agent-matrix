# ADR-030 认知流程可演进但 Stable Kernel 不变

## 状态

ACCEPTED

## 决策

Project Method Intelligence / Capability Discovery 属于可演进 Orchestration Layer。

从 Research-first 切换到 Existing-Method-first 不得要求修改：

```text
Project State
Task Graph
Artifact Lifecycle
Event Bus
Gate
Checkpoint
Worker Adapter Contract
```

## 原因

这是采用 Microkernel + AOP / Extension Runtime 的核心价值之一。
