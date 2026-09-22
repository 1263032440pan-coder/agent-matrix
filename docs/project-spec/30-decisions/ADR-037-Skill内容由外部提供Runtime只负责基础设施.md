# ADR-037 Skill 内容由外部提供，Runtime 只负责基础设施

## 状态

ACCEPTED

## 决策

Project Runtime 产品本身不预装任何具体领域 Skill 作为功能前提。

```text
Project Runtime
= Import / Validate / Registry / Binding / Resolver / Loader / Audit

Skill Content
= User / Organization / Plugin Supplied External Asset
```

初始：

```text
Skill Registry = EMPTY
```

是合法并必须支持的状态。

## 原因

如果 Runtime 内置固定软件开发 Skill 或固定项目方法 Skill：

- Runtime 会与具体业务域耦合；
- 无法验证真正的通用 Skill 平台能力；
- Skill 升级会污染 Core 版本；
- 不同用户的项目方法无法独立管理。

## 结果

D17.5 和 D18 使用 Mock Skill Fixture 验证基础设施，Fixture 不成为产品内置业务 Skill。
