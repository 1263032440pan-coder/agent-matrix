# WP-031 Skill Execution Binding

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED  
> Demo 集成执行：WP-039 / D17D

## 目标

把当前 Project/Task 所需的：

```text
Project Playbook 片段
Resolved Skill（如果有）
Artifact Contract
Research Artifact
Tool Capability
Context
```

绑定为 Worker Work Package 或 Project Method Intelligence Context。

## 前置依赖

```text
WP-009 Worker Adapter
WP-010 Context Builder
WP-027 Skill Registry & Capability Coverage
WP-030 Project Playbook & Planning Context
WP-039 Skill Resolver / Loader
```

## 输入

```text
Task Contract / Method Context
ResolvedSkill / NO_SKILL_REQUIRED
Context Package
Playbook Reference
Artifact Contract
Research References
Capability Snapshot
```

## Skill Loader

```text
SkillLoader.load(...)
→ SkillExecutionContext
```

按需加载：

```text
SKILL.md
references/
schemas/
templates/
```

禁止：

```text
全量 Registry 注入 Worker
Worker 自己从全局 Registry 任意挑 Skill
Skill Loader 修改 Project/Task State
执行 Skill Package 内任意脚本
```

## Skill 可以为空

```text
ResolvedSkill = NONE
```

当：

```text
SkillRequirement.required = false
```

时合法。

Worker 仍可基于：

```text
Task Contract
Playbook
Research
Artifact Contract
Context
```

执行。

## Work Package 新增字段

```text
skill_requirement
resolved_skill_ref
skill_execution_context_ref
actual_skill_id
actual_skill_version
actual_skill_content_hash
```

## Gate

- [ ] Task 可以没有 Skill。
- [ ] Work Package 可携带 Resolved Skill Context。
- [ ] Loader 只加载必要文件。
- [ ] 实际 Skill Version/hash 可审计。
- [ ] Worker 缺 Tool Capability 时返回 CAPABILITY_MISSING。
- [ ] Loader 不修改 Project State。
- [ ] 外部 Skill 不能绕过 Worker/Gate 生命周期。
