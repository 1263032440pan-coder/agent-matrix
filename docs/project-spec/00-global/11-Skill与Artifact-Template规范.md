# 11 Method Asset、External Skill 与 Artifact Template 规范

## 1. Project Method Asset

用户项目级方法资料，包括：

```text
流程 / SOP / 制度 / 实施方案 / 历史项目 / 验收办法 / 模板 / 会议纪要
```

Method Asset 是项目事实来源，不要求满足 Skill Package Schema。

```text
Project Method Asset != Skill
```

## 2. Skill 的系统定位

Skill 是：

> 由用户、组织或 Plugin 后续提供的、可复用、可版本化、AI 可执行的声明式专业方法。

Project Runtime **不内置任何具体领域 Skill 作为启动前提**。

初始：

```text
Skill Registry = EMPTY
```

完全合法。

Skill 内容属于外部资产，Project Runtime 只提供：

```text
Import
Validate
Registry
Binding
Resolution
Loading
Audit
```

## 3. Skill 三层分类

```text
PROJECT_METHOD
TASK_EXECUTION
CAPABILITY
```

### PROJECT_METHOD

描述：

```text
一个完整项目应该如何推进
```

使用位置：

```text
Project Method Intelligence
→ Project Playbook
```

### TASK_EXECUTION

描述：

```text
一个具体类型 Task 应该如何执行
```

使用位置：

```text
Task
→ Skill Resolver
→ Skill Loader
→ Worker Runtime
```

### CAPABILITY

描述：

```text
某项能力应如何可靠使用
```

Capability Skill 本身不创造真实能力。实际能力必须由：

```text
Tool / Worker Runtime / Plugin / Provider
```

提供。

## 4. Skill Package V0.1

统一声明式结构：

```text
skill-package/
├── SKILL.md
├── references/      # optional
├── schemas/         # optional
└── templates/       # optional
```

`SKILL.md` 必须包含 YAML frontmatter：

```yaml
skill_id:
name:
version:
skill_type:
description:
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

Runtime 在安装时额外生成：

```text
source
package_ref
content_hash
status
installed_at
```

### V0.1 安全规则

Skill Package 是声明式资产。

D17.5 不允许通过 Skill Package 直接执行：

```text
.py
.sh
.ps1
.exe
二进制插件
任意安装脚本
```

需要可执行能力时，应通过 Worker / Tool / Plugin / Extension 提供。

## 5. Skill Registry

Registry 回答：

```text
安装了哪些 Skill？
有哪些版本？
是什么类型？
来源是什么？
当前是否 Enabled？
需要哪些 Capability？
```

Registry 不负责决定当前 Task 一定使用哪个 Skill。

## 6. Skill Binding

Project 可以显式固定 Skill 版本：

```text
PROJECT_METHOD
STAGE
TASK_TYPE
TASK
```

已有 Project 默认 `pinned=true`。

```text
安装 Skill v1.1
!=
既有 Project 从 v1.0 自动切换到 v1.1
```

## 7. Skill Resolver

规则优先：

```text
Explicit Binding
>
Explicit Reference / Preferred Skill
>
Exact Metadata Match
>
Generic Fallback
```

只有规则筛选后仍有多个完全等价合法候选时才允许 LLM tie-break。

LLM 不得绕过：

```text
Status
Version
Dependency
Capability
Project Binding
```

## 8. Skill Loader

```text
ResolvedSkill
+
Project / Task Context
↓
SkillExecutionContext
```

Loader 只加载当前所需：

```text
SKILL.md
required references
required schemas
templates（如需要）
```

不得把整个 Registry 放入 Worker Context。

## 9. Project Method Skill 的 Authority

即使用户安装了 Project Method Skill，仍然遵守：

```text
用户明确要求
>
用户 Project Method Asset / Procedure
>
当前项目已确认 Artifact / Decision
>
Installed Project Method Skill
>
Research
>
Model Inference
```

Skill 不能静默覆盖用户 Mandatory 流程。

## 10. Task Execution Skill

Planner 可以声明：

```text
SkillRequirement
```

但 Planner 不直接从 Registry 加载 Skill。

真正流程：

```text
Task
→ Skill Resolver
→ ResolvedSkill / NO_SKILL_REQUIRED
→ Skill Loader
→ Worker Runtime
```

## 11. 无 Skill 场景

如果：

```text
required = false
```

且没有匹配 Skill：

```text
NO_SKILL_REQUIRED
```

Worker 仍可以基于 Task Contract / Playbook / Context 执行。

如果：

```text
required = true
```

且没有合法 Skill：

```text
SKILL_NOT_FOUND
→ Execution Capability Gap
```

不得把 Task 直接判 FAILED。

## 12. Skill 与 Plugin / Extension / Tool

```text
Plugin
= 可安装的软件代码包

Extension
= 注册到 Runtime Extension Point 的运行时能力

Skill
= 外部声明式方法资产

Tool
= 可被调用的实际动作/API/CLI
```

Skill 不得拥有：

```text
DIRECT_STATE_MUTATE
DIRECT_TASKGRAPH_MUTATE
DIRECT_EVENTBUS_MUTATE
```

## 13. Artifact Template

Artifact Template 仍然是可选增强。

没有 Template 时：

```text
Project Playbook
→ Dynamic Artifact Contract
```

Template 不能替代 Acceptance Criteria / Gate。
