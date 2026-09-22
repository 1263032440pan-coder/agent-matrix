# WP-030 Project Playbook 与 Planning Context

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] Playbook 以用户方法为主体。
- [ ] 外部 Research 内容可识别为 RECOMMENDED / OPTIONAL。
- [ ] Model Inference 可识别为 PROPOSED。
- [ ] 任一关键 Workstream 可回答“为什么要做、来源是什么”。

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

- [ ] `Installed Project Method Skills = []` 时仍可生成 Playbook。
- [ ] Skill 不静默覆盖用户 MANDATORY 方法。
- [ ] Playbook 仅记录偏好/绑定，不直接加载 TASK_EXECUTION Skill。
