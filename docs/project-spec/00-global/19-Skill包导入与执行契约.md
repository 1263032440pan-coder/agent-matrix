# 19 Skill Package Import 与 Execution Contract

## 1. 目标

定义 Project Runtime 如何接收用户/组织/Plugin 提供的外部 Skill Package，并安全地：

```text
Import
→ Validate
→ Register
→ Bind
→ Resolve
→ Load
→ Execute
→ Audit
```

本文件不定义任何具体领域 Skill 内容。

## 2. Canonical Skill Package V0.1

```text
skill-package/
├── SKILL.md
├── references/
├── schemas/
└── templates/
```

只有 `SKILL.md` 必需。

## 3. SKILL.md Frontmatter

最小：

```yaml
skill_id: example-skill
name: Example Skill
version: 1.0.0
skill_type: TASK_EXECUTION
description: Example external skill
```

可选：

```yaml
domain: []
stages: []
task_types: []
required_inputs: []
expected_outputs: []
required_capabilities: []
compatible_worker_types: []
dependencies: []
conflicts: []
```

以下字段由 Runtime 生成，包不得强制覆盖：

```text
source
status
package_ref
content_hash
installed_at
installed_by
```

## 4. Import Validation

必须检查：

```text
Archive path traversal
Symbolic-link escape
File count / package size limit
Allowed file types
UTF-8 text validity
Frontmatter schema
skill_id/version uniqueness
Dependency metadata
content_hash
```

V0.1 Skill Package 是声明式资产，禁止导入后自动执行：

```text
.py
.sh
.ps1
.bat
.exe
.dll
.so
binary executable
installer
```

真正执行能力由 Worker / Tool / Plugin 提供。

## 5. Registry Transaction

只有完整 Validate PASS 后才：

```text
Registry Insert
→ status = INSTALLED / ENABLED
→ SKILL_IMPORTED
```

Validation FAIL：

```text
不产生可用 Registry Entry
→ SKILL_VALIDATION_FAILED / SKILL_IMPORT_FAILED
```

## 6. Binding

Project 可以按以下作用域绑定明确版本：

```text
PROJECT_METHOD
STAGE
TASK_TYPE
TASK
```

默认 Pin：

```text
pinned = true
```

## 7. Resolution

固定顺序：

```text
Explicit Binding
→ Explicit Reference / Preferred Skill
→ Exact Metadata Match
→ Generic Fallback
→ NO_SKILL_REQUIRED / SKILL_NOT_FOUND
```

硬过滤：

```text
status
version
dependencies
required capabilities
worker compatibility
conflicts
```

LLM 只能用于硬过滤后完全等价候选的 tie-break。

## 8. Loading

```text
ResolvedSkill
→ verify ID/version/hash
→ read SKILL.md
→ load required references/schemas/templates
→ SkillExecutionContext
```

不得把 Registry 全量 Skill 内容塞入 Worker。

## 9. Execution

Worker Runtime 接受：

```text
Task Contract
Context Package
Optional SkillExecutionContext
```

返回普通 Worker Result，并可附：

```text
skill_id
skill_version
skill_content_hash
outputs
artifact_refs
evidence_refs
```

## 10. State Boundary

任何外部 Skill 都没有：

```text
DIRECT_STATE_MUTATE
DIRECT_TASKGRAPH_MUTATE
DIRECT_EVENTBUS_MUTATE
```

Skill Result 只能成为 Proposal / Artifact / Evidence / Worker Result 输入。

```text
Skill says COMPLETED
!=
Task ACCEPTED
```

最终状态继续由 Core State Machine + Gate 决定。
