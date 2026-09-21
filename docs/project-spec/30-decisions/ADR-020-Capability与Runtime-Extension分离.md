# ADR-020 Capability Extension 与 Runtime Extension 分离

## 状态

ACCEPTED

## 决策

插件扩展分为：

```text
Capability Extension
Runtime Extension
```

两者共用 Plugin Manager，但使用不同 Extension Type、Permission 和 UI 风险展示。

## 原因

新增 Tool 与改变 Project Runtime 生命周期的风险完全不同，必须区分。
