# Demo 与正式架构演进关系

## 原则

```text
Demo 可以简化实现
但不能破坏正式边界
```

## Demo V0.1 → 正式版

### Extension Runtime

Demo：

```text
In-Process ExtensionRegistry
```

正式：

```text
Plugin Manager
plugin.yaml
External Process
JSON-RPC stdio
Permission Manager
Plugin Lifecycle
```

### Research

Demo：

```text
Single ResearchProvider
```

正式：

```text
Research Provider Registry
Web Search / Fetch Providers
Capability Resolution
Research Artifact Lifecycle
```

### Resource Governor

Demo：

```text
max_workers
running_workers
DEFER_RESOURCE
```

正式：

```text
CPU / RAM / Browser / Heavy Task
Worker Resource Profile
Dynamic Resource Decision
```

### Gate

Demo：

```text
ArtifactExistsValidator
OutputContractValidator
LLMReviewValidator
```

正式：

```text
Validator Registry
Security / Compliance / Domain Validators
Evidence Integration
```

### Worker Runtime

Demo：

```text
One Worker Adapter
```

正式：

```text
Multiple WorkerRuntimeAdapter
Provider Resolution
Capability Matching
Health / Lifecycle
```

## 不允许的 Demo 快捷方式

禁止：

```text
Planner 直接改 Project State
Worker 直接把 Task 改成 ACCEPTED
Research 结果直接进入 Planner 而不经过 Playbook
UI 伪造状态
为了 Demo 把 Task Graph 写死
```
