# WP-031 Skill Execution Binding

> 状态：DONE（批次 A 对账收口，2026-09-29——D17D + 试用增量已覆盖全部 Gate，本批补 WorkPackage 三字段）  
> Gate：PASS（7 条 Gate 全过：既有 D17D 测试族 + test_skill_sites_reconciliation.py 补齐项）  
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

## Gate（批次 A 对账 2026-09-29）

- [x] Task 可以没有 Skill（test_skill_loader::test_no_skill_task_executes + 批次 A 补 test_workpackage_none_skill_legal 字段层）
- [x] Work Package 可携带 Resolved Skill Context（D17D skill_context 注入 + 试用增量 ExecutionRunner Phase B-0；批次 A 补三字段 skill_requirement/resolved_skill_ref/skill_execution_context_ref——Runner 注入结构性断言）
- [x] Loader 只加载必要文件（test_e02_load_required_references_only + test_e03_no_full_registry_injection）
- [x] 实际 Skill Version/hash 可审计（test_e05_worker_result_skill_audit——actual_skill_id/version/content_hash 三元组）
- [x] Worker 缺 Tool Capability 时返回 CAPABILITY_MISSING（test_skill_resolver::test_d07）
- [x] Loader 不修改 Project State（批次 A 结构性断言：loader 无 transition_task/StateTransitionService/add(Project/Task 路径）
- [x] 外部 Skill 不能绕过 Worker/Gate 生命周期（test_e06_skill_output_not_accepted——Skill COMPLETED ≠ Task ACCEPTED）

## 对账说明

- Resolver/Loader/注入链: D17C/D17D + 试用增量（Runner Phase B-0 + 项目级通配绑定）
- Work Package 三字段（skill_requirement/resolved_skill_ref/skill_execution_context_ref）: 批次 A 补齐（构造注入 + ExecutionRunner 装配）
- Context Builder（前置 WP-010）: 阶段 5 交付——当前 Context 走 task contract 既有链
