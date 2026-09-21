# WP-010 Context Builder

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 任务目标

为当前 Task 构建最小充分 Context Package。

## 前置依赖

```text
WP-002 Project State
WP-005 Task Graph
WP-009 Worker Adapter
WP-020 Project Memory Manager
WP-021 Hierarchical Summary
WP-024 Retrieval Memory Foundation
WP-030 Project Capability Orchestrator
```

## Context 构造顺序

```text
P0 Current Task / Acceptance / Constraint / Dependency
P1 Decision / Summary / Direct Artifact
P2 Retrieval Result / Recent Evidence
P3 非关键历史
```

实际流程：

```text
Task
→ State Memory
→ Dependency
→ Artifact Relation
→ Summary
→ Retrieval Memory
→ Execution Evidence
→ Token Budget
```

## 禁止

```text
全量加载项目历史
直接访问具体 Retrieval 实现
将 Retrieval Result 当事实
```

## 验收

- [ ] 支持 Token Budget。
- [ ] P0 不被裁剪。
- [ ] 内容去重。
- [ ] 保留 source reference。
- [ ] 可使用 Project Memory Manager.retrieve()。


## Context Extension

Context 构建完成后支持：

```text
context.providers
context.middleware
```

典型：

```text
Domain Context Injection
PII Redaction
Token Compression
```

Middleware 必须返回新 ContextPackage，不得直接修改 Project State。
