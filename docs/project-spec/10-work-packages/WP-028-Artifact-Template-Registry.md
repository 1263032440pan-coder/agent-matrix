# WP-028 Artifact Template Registry

> 状态：DONE（批次 B 交付，2026-09-29——独立新域 artifact_template + 与 delivery_contract 衔接点登记）  
> Gate：PASS（依据 tests/test_artifact_template.py 7 用例）

## 目标

管理可选的 Artifact Template，并支持没有模板时生成项目级 Artifact Contract。

## 前置依赖

```text
WP-012 Artifact Manager
WP-026 Project Understanding
```

## 关键原则

Template Registry 可以为空。

如果 Playbook 认为项目需要某类产物，但没有现成模板：

```text
Project Playbook
→ Dynamic Artifact Contract
```

而不是 BLOCKED。

## 验收

- [x] Template 可版本化（(template_id, version) 唯一约束 + 重复导入拒绝 + 多版本共存 + disable 摘除——skill 模式对齐）
- [x] Template 可导入（import_template + content_hash + ENABLED 入库；三防：扩展名白名单 6 类/路径穿越双模式拒绝/512KB 上限）
- [x] Template 为空时系统仍可运行（空 Registry 全链实测——全部产物走 Dynamic Contract，无一条 BLOCKED）
- [x] 支持 Dynamic Artifact Contract（模板命中→TEMPLATE 引用（template_ref + structure + body hint）；miss→PLAYBOOK/REQUIREMENT 动态 spec；幂等重复生成零新建）

## 设计决策（批次 B 前置）

**独立新域 + 衔接**：ArtifactTemplate/DynamicArtifactContract 与 delivery_contract 独立（规格 vs 验收追踪两个关注点）；Dynamic Contract 产物名可作为 DeliveryItem.requirement_ref 来源（衔接点登记，V0.1 不强制）

## 实现说明

- `project_runtime/artifact_template/`（models: ArtifactTemplate 版本化资产 + DynamicArtifactContract 项目级规格 / registry: Registry 三防导入 + DynamicContractService 生成路径）
- wanted 双来源: Playbook workstream outputs + WP-026 known_requirements（预告⑤ 衔接——含产物关键词陈述进 REQUIREMENT 来源）
- migration e6f2b4d8a1c9
