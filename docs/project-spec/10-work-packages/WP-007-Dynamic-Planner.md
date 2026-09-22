# WP-007 Dynamic Planner

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

根据 Planning Context 动态生成和调整 Task Graph。

## 前置依赖

```text
WP-005 Task Graph
WP-006 Current Frontier
WP-030 Project Playbook 与 Planning Context
```

## 正式输入

```text
PlanningContext
```

其中包含 Project Playbook。

## Planner 不负责

```text
自己随意联网搜索
自己扫描全部 Skill
自己假设客户私有事实
```

需要新知识时输出：

```text
REQUEST_RESEARCH
REQUEST_USER_INPUT
```

并由 Research Manager 执行。

## Apply Pipeline

```text
Candidate Plan
→ Schema Validation
→ Graph Validation
→ Cycle Check
→ Policy Validation
→ Apply
```

## 验收

- [ ] 无 Skill 的 Planning Context 可正常规划。
- [ ] Task 可引用 Playbook / Research。
- [ ] 不硬编码任何行业阶段。
- [ ] Planner 需要更多知识时会返回 Research Request。

## V1.9 Provenance / Authority 规则

Planner 接收 Playbook 时必须：

```text
MANDATORY → 不得静默删除
RECOMMENDED → 根据项目状态决定是否纳入
OPTIONAL → 可按价值/成本选择
PROPOSED → 未确认时不得伪装成用户硬要求
```

## V1.12 SkillRequirement

Planner 可为 Task 声明：

```text
skill_requirement
```

例如：

```json
{
  "skill_type": "TASK_EXECUTION",
  "required": false,
  "preferred_skill_id": null,
  "domain": "software-development",
  "task_type": "implementation",
  "required_capabilities": ["file_write", "test_execution"]
}
```

Planner 负责描述需求，不负责：

```text
扫描 Registry
安装 Skill
决定最终 Skill Version
读取 SKILL.md
```

最终由 Skill Resolver / Loader 处理。

### Gate 增补

- [ ] 无 Skill Requirement 的 Task 仍可规划。
- [ ] `required=false` 可生成无 Skill Task。
- [ ] Planner 不依赖任何固定 Skill ID。
