# WP-037 Project Method Asset Discovery 与 Procedure Normalization

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

发现用户已有项目方法资产，并将其转换为 Project Procedure Model。

## 前置依赖

```text
WP-004 State Discovery
WP-020 Project Memory Manager
WP-026 Project Understanding
```

## Method Asset 类型

```text
PROCESS
SOP
STANDARD
TEMPLATE
HISTORICAL_REFERENCE
ACCEPTANCE_RULE
ROLE_DEFINITION
DELIVERY_RULE
```

## Procedure Node

至少抽取：

```text
input
activity
output
role
dependency
gate
acceptance
evidence
constraint
rework_path
provenance
```

无法确认的字段必须保留 UNKNOWN / AMBIGUOUS，不允许脑补成事实。

## Extension Points

```text
method.assets.providers
method.assets.processors
procedure.normalizers
```

## Gate

- [ ] 能从多个文档中发现流程资产。
- [ ] 能建立 Procedure Model。
- [ ] 每个节点可追溯 source_ref。
- [ ] 未明确 Acceptance 时保持 UNKNOWN。
- [ ] 冲突流程可标记 CONFLICTED。
