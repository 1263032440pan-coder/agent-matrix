# WP-033 Capability Extension Framework

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

建立 Agent Capability Plane 的统一 Provider Registry 与调用 Contract。

## 前置依赖

```text
WP-032 Plugin Manager 与 Manifest
```

## Capability Extension 类型

```text
Tool Provider
Retrieval Provider
Research Provider
Worker Runtime Provider
Knowledge Provider
LLM Provider
Artifact Processor
```

## 核心接口

```text
register_provider()
unregister_provider()
list_providers()
health()
invoke()
```

## 要求

Provider 必须通过 Extension Runtime 调用，不允许核心业务模块直接 import 外部插件实现。

## 验收

- [ ] Plugin 可注册 Capability Extension。
- [ ] Provider 可按 capability 查询。
- [ ] Provider 异常有统一错误。
- [ ] 支持优先级与 enable/disable。
- [ ] 核心模块只依赖 Provider Contract。
