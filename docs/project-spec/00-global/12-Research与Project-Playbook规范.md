# 12 Research-on-Gap 与 Project Playbook 规范

## Research 不是默认第一步

在创建新 Research 前必须依次检查：

```text
User Method Assets
Project Procedure Model
Project Memory
Installed Skills
Existing Research
```

## Gap Routing

```text
PRIVATE_GAP
→ User Clarification

EXPLICITNESS_GAP
→ Model Proposal / User Confirm

DOMAIN_GAP
→ Domain Research

TECHNICAL_GAP
→ Technical Research

PROCESS_GAP
→ Project Method Research
```

## Research 约束

Research 不能覆盖用户 MANDATORY 流程。

外部研究默认只能形成：

```text
RECOMMENDED / OPTIONAL
```

除非用户明确采纳，否则不能升级为 MANDATORY。

## Playbook

Playbook 来源：

```text
Project Understanding
+ Method Assets
+ Procedure Model
+ Skills
+ Memory
+ Gap Research
+ Model Proposals
```

必须维护：

```text
method_asset_refs
procedure_model_version
provenance_map
gap_refs
research_refs
skill_refs
confidence
version
```
