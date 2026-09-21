# 13 Extension Runtime 与插件体系

## 1. 目标

不是提前猜未来有哪些插件，而是为未知扩展提供统一机制。

## 2. Plugin Package

```text
Plugin Package
├── plugin.yaml
├── executable / launcher
└── resources
```

一个 Plugin 可以注册多个 Extension。

## 3. 两类 Extension

### Capability Extension

```text
Tool Provider
Retrieval Provider
Research Provider
Worker Runtime Provider
Knowledge Provider
LLM Provider
Artifact Processor
```

### Runtime Extension

```text
Observer
Validator
Middleware
Hook
Policy Extension
```

## 4. 插件进程隔离

默认：

```text
External Process
JSON-RPC 2.0 over stdio
```

Core 负责：

```text
启动
健康检查
超时
重启
禁用
卸载
```

## 5. Plugin Registry

持久化：

```text
plugin_id
version
enabled
permissions
extensions
health
api_version
```

## 6. 示例

Semantic Retrieval：

```text
Plugin Package
→ Capability Extension
→ retrieval.providers
```

Security Review：

```text
Plugin Package
├── Capability: security.providers
├── Hook: worker.after_execute
└── Validator: gate.validators
```


## Resource-Aware Plugin Lifecycle

Plugin Manifest 可声明：

```yaml
runtime:
  lifecycle: ON_DEMAND
  memory_estimate_mb: 300
  max_parallel_instances: 1
```

允许：

```text
ALWAYS_ON
ON_DEMAND
PER_TASK
```

Resource Governor 可以限制可选插件进程并发，但不能改变插件的功能语义。
