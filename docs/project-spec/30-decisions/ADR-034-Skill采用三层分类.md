# ADR-034 Skill 采用三层分类

## 状态

ACCEPTED

## 决策

Skill 分类固定为：

```text
PROJECT_METHOD
TASK_EXECUTION
CAPABILITY
```

- PROJECT_METHOD：项目整体推进方法。
- TASK_EXECUTION：具体 Task 执行方法。
- CAPABILITY：具体能力的使用方法。

## 原因

避免 Project Method、Task Prompt 和能力使用 SOP 混在同一个 Skill 池中，使 Registry / Resolver / Loader 能在正确层级使用 Skill。
