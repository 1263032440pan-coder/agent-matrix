# 17 Project Method Assets 与 AI 可执行度

## 1. 设计原则

默认：

```text
USER_HAS_METHOD_ASSETS = TRUE / LIKELY
```

但绝不默认：

```text
METHOD_ASSETS_ARE_AI_READY = TRUE
```

## 2. Procedure Node Schema

```text
procedure_id
name
workstream
inputs
activities
outputs
roles
dependencies
gate
acceptance_criteria
evidence_requirements
constraints
rework_path
source_refs
authority
readiness_status
```

## 3. Readiness 状态

```text
READY
PARTIAL
AMBIGUOUS
CONFLICTED
MISSING
```

## 4. Method Readiness 维度

```text
Scope Coverage
Action Explicitness
Artifact Definition
Acceptance Definition
Dependency / Transition
Constraint / Authority
```

## 5. Provenance

每条方法结论必须能回答：

```text
Who said this?
Where did it come from?
Is it mandatory?
How confident are we?
```

字段：

```text
source_type
source_ref
source_version
confidence
authority
```

## 6. Authority

```text
MANDATORY
RECOMMENDED
OPTIONAL
PROPOSED
```
