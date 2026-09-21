# WP-030 Project Playbook 与 Planning Context

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

把用户已有项目方法作为主体，融合 Skill、Memory、Gap Research 和 AI Proposal，形成当前项目最终可执行 Playbook。

## 前置依赖

```text
WP-037 Project Method Asset Discovery & Procedure Normalization
WP-038 Project Readiness Assessment & Gap Classification
WP-029 Research Manager & Gap Resolution
```

## Playbook 新增字段

```text
method_asset_refs
procedure_model_version
method_readiness_snapshot
execution_readiness_snapshot
provenance_map
gap_refs
resolved_gap_refs
unresolved_gaps
```

## Authority

```text
MANDATORY
RECOMMENDED
OPTIONAL
PROPOSED
```

Planner 必须尊重 MANDATORY。

## Gate

- [ ] Playbook 以用户方法为主体。
- [ ] 外部 Research 内容可识别为 RECOMMENDED / OPTIONAL。
- [ ] Model Inference 可识别为 PROPOSED。
- [ ] 任一关键 Workstream 可回答“为什么要做、来源是什么”。
