# CR-SKILL-001 External Skill Runtime Infrastructure

## 状态

```text
APPROVED
```

## 发现阶段

```text
D17 完成后 / D18 开始前
```

## 问题

现有 V1.11 已有 Skill Registry / Execution Binding 概念，但缺少：

```text
统一 Skill Package
安全 Import / Validation
Skill 三层分类
Version Pin
Rule-first Resolver
Skill Loader
Worker Skill Audit
```

同时需要明确一个边界：

> 具体 Skill 内容不是 Project Runtime 项目本身的一部分，而是系统完成后由用户、组织或 Plugin 提供的外部资产。

## 变更

增加：

```text
D17A Skill Registry
D17B Skill Package Import + Validation
D17C Skill Resolver + Project Binding
D17D Skill Loader + Worker Execution Integration
```

新增 `WP-039` 并增强相关 Global/WP/RTM/E2E 文档。

## 不变项

```text
D01～D17 = FROZEN
Stable Kernel 不变
Task FSM 不变
EXECUTION_DONE != ACCEPTED 不变
Worker Runtime 解耦原则不变
```

## 排除项

不把任何具体：

```text
software-project-delivery
software-developer
software-reviewer
software-repair
```

或其他领域 Skill 放入 Project Runtime 正式产品内容。
