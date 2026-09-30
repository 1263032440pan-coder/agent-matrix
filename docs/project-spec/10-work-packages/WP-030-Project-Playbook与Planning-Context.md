# WP-030 Project Playbook 与 Planning Context

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_playbook_fusion.py 8 用例 + 既有 playbook/planner 16 用例零回归）

## 目标

把用户已有项目方法作为主体，融合 Skill、Memory、Gap Research 和 AI Proposal，形成当前项目最终可执行 Playbook。

## 前置依赖

```text
WP-037 Project Method Asset Discovery & Procedure Normalization
WP-038 Project Readiness Assessment & Gap Classification
WP-029 Research Manager & Gap Resolution
```

## Playbook 新增字段

```text
method_asset_refs
procedure_model_version
method_readiness_snapshot
execution_readiness_snapshot
provenance_map
gap_refs
resolved_gap_refs
unresolved_gaps
```

## Authority

```text
MANDATORY
RECOMMENDED
OPTIONAL
PROPOSED
```

Planner 必须尊重 MANDATORY。

## Gate

- [x] Playbook 以用户方法为主体（workstream 主体字段实测逐字来自用户文档：inputs/outputs/activities；source.type=USER_METHOD）
- [x] 外部 Research 内容可识别为 RECOMMENDED / OPTIONAL（research_enrichments 附挂 authority=RECOMMENDED 只增不改——用户字段与 source 原样；skill refs authority=OPTIONAL）
- [x] Model Inference 可识别为 PROPOSED（no-asset → MODEL_INFERENCE；planner 按 PROPOSED 权威处理为 D09 既有链）
- [x] 任一关键 Workstream 可回答“为什么要做、来源是什么”（每 workstream source 含 asset_id/procedure_node_id/authority 三元组——为什么/来源/什么级别全可答）

## V1.12 External Project Method Skill

Project Playbook 可引用已安装的 `PROJECT_METHOD` Skill，但它们属于外部可选输入。

Authority：

```text
用户明确要求
>
用户 Method Asset / Procedure
>
当前项目已确认 Artifact / Decision
>
Installed Project Method Skill
>
Research
>
Model Inference
```

Playbook 可新增：

```text
project_method_skill_refs[]
task_skill_preferences[]
```

Skill Reference 必须带：

```text
skill_id
version
content_hash
source
```

Gate 增补：

- [x] `Installed Project Method Skills = []` 时仍可生成 Playbook（零 skill 绑定实测全链生成）
- [x] Skill 不静默覆盖用户 MANDATORY 方法（绑定 PROJECT_METHOD skill 重建——主体字段不动、skill 只进 refs 列表 authority=OPTIONAL）
- [x] Playbook 仅记录偏好/绑定，不直接加载 TASK_EXECUTION Skill（结构性断言：builder 无 SkillLoader/SkillResolver/load 路径——加载属执行链 resolver）

## 实现说明

- Builder 升级: playbook.build.before/after 双点 fire（advisory）+ research 融合（ANSWERED ResearchRecord → workstream.research_enrichments，RECOMMENDED 只增不改）+ skill refs（ENABLED PROJECT_METHOD 绑定 → project_method_skill_refs[]，四必填字段 skill_id/version/content_hash/source）+ execution_readiness_snapshot 接 WP-038 双轴（D11 占位 None 终结）+ resolved_gap_refs 按 gap.status 实算
- 模型: project_playbook + project_method_skill_refs/task_skill_preferences 两列（migration d5e8a3c1f7b4）
- Planning Context 消费: 双 readiness 快照入 playbook（WP-038→030 链路接通）
