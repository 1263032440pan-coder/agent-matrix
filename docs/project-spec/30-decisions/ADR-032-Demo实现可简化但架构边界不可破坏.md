# ADR-032 Demo 实现可简化，但架构边界不可破坏

## 状态

ACCEPTED

## 决策

允许 Demo V0.1 使用：

```text
Single Provider
One Worker
Resource Governor Lite
In-Process Extension Registry
Simple Validators
```

但禁止：

```text
Worker 直接写 Project State
Planner 直接修改 Task 状态
Research 跳过 Playbook 直接驱动 Planner
UI 使用假状态冒充真实执行
Task Graph 写死
```

## 原因

Demo 的目的不是临时做一套不可演进的原型，而是用最小实现验证正式架构。
