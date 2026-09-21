# 11 Method Asset、Skill 与 Artifact Template 规范

## Project Method Asset

用户项目级方法资料，包括：

```text
流程 / SOP / 制度 / 实施方案 / 历史项目 / 验收办法 / 模板 / 会议纪要
```

Method Asset 是原始事实来源，不要求预先满足 Skill Schema。

## Skill

Skill 是：

```text
可复用、可版本化、经过验证、AI 可执行的专业 SOP
```

Method Asset ≠ Skill。

用户资料可以经过长期验证后沉淀成 Candidate Skill，但不能导入即自动升级为可信全局 Skill。

## SkillDefinition

```yaml
skill_id:
name:
version:
description:
source:
required_inputs: []
procedure: []
expected_outputs: []
acceptance_criteria: []
required_capabilities: []
```

## Artifact Template

仍然是可选增强。

没有 Template 时：

```text
Project Playbook
→ Dynamic Artifact Contract
```
