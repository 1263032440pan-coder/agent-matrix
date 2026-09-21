# ADR-019 统一 Extension Runtime

## 状态

ACCEPTED

## 决策

不再仅针对已知能力预留独立插件接口。

Project Runtime 提供统一：

```text
Plugin Manager
Extension Registry
Provider / Hook / Middleware / Validator / Observer Registry
```

未来未知扩展优先通过 Extension Runtime 接入。

## 原因

降低开发完成后新增能力对核心代码的破坏性修改。
