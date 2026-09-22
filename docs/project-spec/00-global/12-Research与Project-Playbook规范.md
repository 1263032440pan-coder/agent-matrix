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

## V1.12 Installed Skill 边界

本文件中的：

```text
Installed Skills
```

专指用户/组织/Plugin 已导入 Skill Registry 的外部 Skill，不代表 Project Runtime 内置固定 Skill 集。

Research 前检查顺序保持：

```text
User Method Assets
Project Procedure Model
Project Memory
Installed Skills（如果存在）
Existing Research
```

如果 `Installed Skills = []`，直接继续下一项，不得阻塞 Research / Playbook。

Playbook 中的 `skill_refs` 必须保存：

```text
skill_id
version
content_hash
source
```

以保证可追溯和可复现。
