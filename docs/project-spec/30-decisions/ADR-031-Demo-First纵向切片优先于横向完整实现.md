# ADR-031 Demo-First 纵向切片优先于横向完整实现

## 状态

ACCEPTED

## 决策

首版开发采用：

```text
Vertical Slice First
```

而不是：

```text
Layer Complete First
```

即先跑通：

```text
Method Asset
→ Readiness
→ Playbook
→ Task Graph
→ Worker
→ Artifact
→ Gate
```

再扩展完整插件、Memory、Traceability 与多 Worker。

## 原因

本项目复杂度高。如果按照所有 WP 横向逐层完成，产品价值长期不可见。

纵向切片可以：

- 更早验证架构边界。
- 更早验证核心产品假设。
- 更早获得领导和用户反馈。
- 防止在未验证产品闭环前过度工程化。
