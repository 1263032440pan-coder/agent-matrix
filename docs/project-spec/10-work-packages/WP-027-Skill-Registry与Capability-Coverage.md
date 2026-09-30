# WP-027 Skill Registry 与 Execution Capability Readiness

> 状态：DONE（批次 A 对账收口，2026-09-29——D17A-C 实质交付 + WP-038 承接 Execution Capability Readiness + 本批补齐 Gate 7/8/9 专项与 spec 六字段对齐）  
> Gate：PASS（Registry 9 条 Gate 全过：D17A-C 测试 + WP-038 双轴 + test_skill_sites_reconciliation.py 补齐项）  
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

## Gate（批次 A 对账 2026-09-29）

- [x] Registry 为空时 Runtime 正常工作（test_skill_registry::test_a01）
- [x] 可区分三种 SkillType（test_c01）
- [x] `(skill_id, version)` 唯一（test_unique_skill_id_version + uq 约束）
- [x] Disabled/Invalid Skill 不可自动使用（test_c02_c03 + resolver preferred 路径过滤）
- [x] Project 可绑定明确 Skill Version（test_c04_project_binding_pinned）
- [x] 安装新版本不改变已有 Project Pin（test_d04_pin_no_drift）
- [x] 用户方法完整但无 Skill 时可判 Method Ready（批次 A 补 test_gate7——方法轴高覆盖与 Skill 数无关、Skill=0 时 Execution 仍 READY）
- [x] 方法明确但缺 required Skill / Tool / Worker 时判 Execution Gap（批次 A 补 test_gate8——浏览器工具缺失→CAPABILITY gap 精确指名，方法轴不受牵连；Execution Capability Readiness 本体由 WP-038 交付）
- [x] 不把 LLM 推断自动注册为可信 Skill（批次 A 补 test_gate9 双防线——认知链三模块结构性断言无 Skill 注册路径 + SkillSource 枚举不含 LLM 来源）

## 对账说明（归属厘清）

- Registry/Binding/Version Pin: D17A-C 交付（tests/test_skill_registry.py 9 用例）
- Execution Capability Readiness: **WP-038 交付**（双轴 readiness——本 WP spec 的六输出字段已在 WP-038 的 execution_capability_readiness JSON 内对齐：covered_by_worker/covered_by_tool/covered_by_skill/missing_capabilities/missing_skill_dependencies/execution_readiness，批次 A 补齐字段名专项断言）
- 安全导入: WP-039/D17B Importer（Registry 不解压不可信 ZIP——既有实现边界）
