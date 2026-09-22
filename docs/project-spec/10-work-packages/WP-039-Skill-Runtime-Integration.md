# WP-039 Skill Runtime Integration

> Demo 阶段：D17.5  
> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED  
> 前提：D01～D17 已完成并冻结。

## 1. 目标

在不重构 Stable Kernel、Task FSM 和 WorkerRuntimeAdapter 基础契约的前提下，接入通用 External Skill Runtime Infrastructure：

```text
External Skill Package
→ Importer
→ Validator
→ Registry / Binding
→ Resolver
→ Loader
→ Worker / Project Method Intelligence
```

Project Runtime 不内置任何具体领域 Skill。

## 2. 复用既有 WP

```text
WP-027 Skill Registry & Capability Coverage
WP-031 Skill Execution Binding
WP-030 Project Playbook
WP-007 Planner
WP-008 Scheduler
WP-009 Worker Adapter
WP-014 Gate
```

WP-039 是 D17.5 的 Demo 集成总包。

## 3. D17A Skill Registry

### 实现

```text
SkillType
SkillDefinition
SkillStatus
SkillSource
SkillBinding
Version Pin
Registry Repository
```

### 建议目录

```text
project_runtime/skills/
├── models.py
├── registry.py
├── repository.py
└── bindings.py
```

### Gate

- [ ] Registry 初始为空仍可正常启动。
- [ ] 支持三种 SkillType。
- [ ] `(skill_id, version)` 唯一。
- [ ] 支持 Enable/Disable。
- [ ] Project Binding 固定明确版本。
- [ ] Invalid/Disabled Skill 不可自动使用。

## 4. D17B Skill Package Import + Validation

### 实现

```text
SkillPackageImporter
SkillPackageValidator
SafeExtract
SKILL.md Frontmatter Parser
Content Hash
```

V0.1 输入：

```text
Local Directory
ZIP
```

### 安全硬规则

- 防 ZIP Path Traversal。
- 防 Symlink Escape。
- 限制包大小和文件数。
- 只允许声明式文本资源。
- 不执行包内脚本/二进制。
- Validate 完成前不得进入可用 Registry。

### Gate

- [ ] 合法 Mock Skill 可导入。
- [ ] Frontmatter 错误被拒绝。
- [ ] Path Traversal 被拒绝。
- [ ] 可执行文件包被拒绝或 INVALID。
- [ ] content_hash 稳定可复现。

## 5. D17C Skill Resolver + Project Binding

### 接口

```text
SkillResolver.resolve(context, requirement, bindings, capability_snapshot)
→ SkillResolutionResult
```

### 规则

```text
1 Explicit Binding
2 Explicit Reference / Preferred Skill
3 Exact Metadata Match
4 Generic Fallback
5 NO_SKILL_REQUIRED / SKILL_NOT_FOUND
```

只有完全等价合法候选才允许 LLM tie-break。

### Gate

- [ ] 规则能确定时不调用 LLM。
- [ ] Required Skill 缺失返回 SKILL_NOT_FOUND。
- [ ] Optional Skill 缺失返回 NO_SKILL_REQUIRED。
- [ ] Capability/Dependency 缺失可区分。
- [ ] Version Pin 生效。

## 6. D17D Skill Loader + Worker Execution Integration

### 实现

```text
ResolvedSkill
+ Task/Method Context
→ SkillLoader
→ SkillExecutionContext
→ WorkerRuntimeAdapter / Method Intelligence
```

Loader 按需加载：

```text
SKILL.md
references/
schemas/
templates/
```

### Worker 追踪

```text
actual_skill_id
actual_skill_version
actual_skill_content_hash
```

### UI 最小增量

不重做 D17，只增加可观察字段：

```text
Resolved Skill
Skill Version
Resolution Reason
Skill Load Status
```

### Gate

- [ ] Loader 不全量加载 Registry。
- [ ] No Skill Task 可执行。
- [ ] Skill Context 可传给 Worker。
- [ ] Worker Result 可追踪版本/hash。
- [ ] Skill 内容不能直接修改 Project/Task State。
- [ ] Skill Result 不能绕过 Gate。

## 7. 测试 Skill

D17.5 / D18 使用专门 Mock Skill Fixture 验证 Runtime，不把 Fixture 作为产品内置 Skill。

Mock Skill 仅验证：

```text
Import
Validate
Registry
Bind
Resolve
Load
Execute
```

不承载正式软件开发业务流程。

## 8. 禁止范围

D17.5 不做：

```text
Skill Marketplace
远程 Skill Store
在线自动升级
Skill 商店 UI
复杂推荐算法
Skill 包执行脚本
内置软件开发 Skill
内置项目方法 Skill
```

## 9. 完成定义

```text
D17A PASS
AND D17B PASS
AND D17C PASS
AND D17D PASS
→ WP-039 PASS
→ Current Frontier = D18
```
