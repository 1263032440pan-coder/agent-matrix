# WP-031 Execution Binding

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

把当前 Task 所需的：

```text
Project Playbook 片段
Skill（如果有）
Artifact Contract
Research Artifact
Tool Capability
Context
```

绑定成 Worker Work Package。

## 前置依赖

```text
WP-009 Worker Adapter
WP-010 Context Builder
WP-030 Project Playbook 与 Planning Context
```

## Skill 可以为空

```text
selected_skills = []
```

Worker 仍可基于：

```text
Task Contract
Playbook
Research
Artifact Contract
```

执行。

## 验收

- [ ] Task 可以没有 Skill。
- [ ] Work Package 可带 Playbook Reference。
- [ ] Research Task 可要求 WEB_SEARCH / WEB_FETCH。
- [ ] Worker 缺 Tool Capability 时返回 CAPABILITY_MISSING。
