# WP-038 Project Readiness Assessment 与 Gap Classification

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_readiness_dual_axis.py 11 用例 + 既有 readiness/gap 16 用例适配零回归）

## 目标

分别评估：

```text
Method Readiness
Execution Capability Readiness
```

并生成精确 Gap。

## 前置依赖

```text
WP-027 Skill Registry & Execution Capability Readiness
WP-037 Project Method Asset Discovery & Procedure Normalization
```

## Method Readiness 维度

```text
Scope Coverage
Action Explicitness
Artifact Definition
Acceptance Definition
Dependency / Transition
Constraint / Authority
```

## 节点状态

```text
READY
PARTIAL
AMBIGUOUS
CONFLICTED
MISSING
```

## Gap Type

```text
PRIVATE
EXPLICITNESS
DOMAIN
TECHNICAL
PROCESS
CAPABILITY
```

## Extension Points

```text
method.readiness.evaluators
gap.classifiers
gap.resolvers
```

## Gate

- [x] 有大量文档不等于自动判 READY（verbose-thin 对照组实测：文字量大但六维度缺失 → NOT READY + gaps 齐发；rich 文档 coverage 显著更高）
- [x] 能识别人类可理解但 AI 不可执行的流程缺口（半定义节点「组织评审」→ gap 指名缺 scope_coverage/acceptance_definition 维度，非笼统「无法执行」；全部 source_ref 可追溯）
- [x] Method / Execution Readiness 可分别为 READY / MISSING（双轴独立实测：thin doc → Method PARTIAL/MISSING 且 Execution READY；声明浏览器能力无 provider → Execution PARTIAL + CAPABILITY gap）
- [x] Skill=0 本身不触发 Research（零 skill 绑定项目 Execution 仍 READY、execution_gaps 空、无 CAPABILITY 脑补 gap；结构性断言 readiness/gap 模块无 Research 触发路径——WP-029 域不越界）

## 实现说明

- `readiness/execution_capability.py`: ExecutionCapabilityReadiness——节点声明能力线索（工具/技能关键词）vs 运行时实际可用（内置 LLM Worker 恒在 + ENABLED skill 绑定）；requirements 是「节点真正要求的」而非固定清单（10-规范规则 5）
- Assessor 双轴组合: 同一 assessment 快照填充 execution_capability_readiness（D08 占位 None 终结）+ execution_gaps 并入 gaps JSON
- Gap 六类型映射深化: artifact_definition/acceptance_definition → PRIVATE、constraint_authority → TECHNICAL（不再是单一 EXPLICITNESS）
- Extension 三点登记: method.readiness.evaluators（链上真实 fire）/ gap.classifiers / gap.resolvers（WP-029 消费）——STABLE_POINTS 24→27
- D08 老断言 test_execution_capability_is_null 语义更新为 now_filled（占位终结的正向验证）
