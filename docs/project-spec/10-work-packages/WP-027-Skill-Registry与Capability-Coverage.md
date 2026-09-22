# WP-027 Skill Registry 与 Execution Capability Readiness

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED  
> Demo 集成执行：WP-039 / D17A～D17C

## 目标

建立外部 Skill 的 Registry / Binding / Coverage 基础能力，并评估当前项目是否具备执行 Procedure / Task 所需的 Skill、Worker、Tool、Provider。

## 关键原则

```text
Method Asset != Skill
Skill Content != Project Runtime Built-in Logic
Skill Registry = EMPTY 仍然合法
Skill=0 不代表 Method Readiness 低
```

## Skill 分类

```text
PROJECT_METHOD
TASK_EXECUTION
CAPABILITY
```

## Registry 管理对象

使用 `00-global/03-核心领域模型.md` 中：

```text
SkillDefinition
SkillBinding
SkillStatus
SkillSource
```

## Registry 最小能力

```text
register
get
list
validate metadata
enable
disable
version lookup
project binding
content_hash lookup
```

注意：Skill Package 的安全导入由 WP-039 D17B 的 Importer / Validator 完成，Registry 不直接解压不可信 ZIP。

## Version Pin

Project 绑定 Skill 后默认：

```text
pinned = true
```

安装新版 Skill 不自动改变正在运行 Project 的 Binding。

## Execution Capability Readiness

检查：

```text
required worker runtime
required tool/provider
required skill
skill dependencies
credential / permission
runtime capability
```

输出：

```text
covered_by_worker
covered_by_tool
covered_by_skill
missing_capabilities
missing_skill_dependencies
execution_readiness
```

## Gate

- [ ] Registry 为空时 Runtime 正常工作。
- [ ] 可区分三种 SkillType。
- [ ] `(skill_id, version)` 唯一。
- [ ] Disabled/Invalid Skill 不可自动使用。
- [ ] Project 可绑定明确 Skill Version。
- [ ] 安装新版本不改变已有 Project Pin。
- [ ] 用户方法完整但无 Skill 时可判 Method Ready。
- [ ] 方法明确但缺 required Skill / Tool / Worker 时判 Execution Gap。
- [ ] 不把 LLM 推断自动注册为可信 Skill。
