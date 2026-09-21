# WP-028 Artifact Template Registry

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] Template 可版本化。
- [ ] Template 可导入。
- [ ] Template 为空时系统仍可运行。
- [ ] 支持 Dynamic Artifact Contract。
