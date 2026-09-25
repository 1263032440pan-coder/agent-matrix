# WP-033 Capability Extension Framework

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_capability_extension.py 8 用例，真实插件进程经 D16 门面全链）

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

- [x] Plugin 可注册 Capability Extension（test_plugin_registers_capability_via_bridge：真实 stdio 插件进程启动后经 PluginBridge 自动注册进 D16 门面）
- [x] Provider 可按 capability 查询（按 TOOL/RETRIEVAL 精确过滤；manifest point→CapabilityType 映射，runtime 类 point 跳过）
- [x] Provider 异常有统一错误（无 provider→ExtensionUnavailableError；RPC 死进程/断管→ExtensionError 包装；插件侧 error→ExtensionError）
- [x] 支持优先级与 enable/disable（in-process provider(100) 与插件 provider(50) 共存按优先级调度；门面 set_enabled 控制插件来源 provider）
- [x] 核心模块只依赖 Provider Contract（invoke 全程走 D16 ExtensionRegistry 门面，核心不知插件存在）

## SC01 桥接设计（WP-032 复验衔接点）

D16 ExtensionRegistry 为**统一门面**（契约不变），Plugin 为 provider **来源之一**（分层不合并）：
- `plugin/capability.py`: PluginCapabilityProvider（D16 Provider 协议实现）+ manifest_to_providers（point→CapabilityType 映射：tool/retrieval/research/worker/knowledge/llm/artifact.providers→对应枚举；权限 READ_ONLY/ENRICH/VALIDATE/CONTROL 映射）
- `plugin/bridge.py`: PluginBridge——start 时注册/stop-disable-uninstall 时摘除/restart 时 rebind
- Manager.start 返回 bridged_providers；无第三套注册表
