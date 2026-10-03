# WP-010 Context Builder

> 状态：DONE（2026-09-29，阶段 5 首站——全项目最后一个大站）  
> Gate：PASS（依据 tests/test_context_builder.py 12 用例）

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

- [x] 支持 Token Budget（budget_tokens 硬约束；超限丢序 P3→P2→P1 非方法段（history→summary→artifact）；truncated + dropped_kinds 可审计——P1 超预算也截断实测）
- [x] P0 不被裁剪（极小预算 40 token 实测：任务/验收/约束/依赖四段全保；relaxed 预算下 P0 恒等不变）
- [x] 内容去重（seen_content 集合——同内容跨来源只进一次，专项断言）
- [x] 保留 source reference（每段 source_ref：task:/procedure_node:/artifact:/summary:/retrieval:/event: 全可溯）
- [x] 可使用 Project Memory Manager.retrieve()（P2 检索经统一门面——结构性断言禁 FTS5Retriever/Ripgrep/Path 直连）


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

## 实现说明（2026-09-29）

- `project_runtime/context/`（package: ContextPackage/ContextSection frozen dataclass——middleware 原地改在类型层不可能；builder: ContextBuilder 四优先级构造 + 预算实施 + 去重）
- 构造链: State Memory → Dependency → Procedure 节点方法上下文（验收/约束/活动）→ Summary → 直接上游 Artifact → Retrieval（非事实标注）→ Execution Evidence → History（limit 截断）
- extension: context.providers / context.middleware 双点 fire（middleware 归一：非 ContextPackage 返回一律忽略保 fallback）
- Worker 接线: ExecutionRunner Worker 前构建 ContextPackage → to_json() 注入 WorkPackage.contract（planning→execution 桥——WP-028 复验预告③ 绑定方式落地）
- 检索结果恒带 [检索参考·非事实] 标注（禁止当事实红线）
