# WP-029 Research Manager 与 Gap Resolution

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 目标

只对已经明确分类的 Gap 进行正确路由。

## 前置依赖

```text
WP-020 Project Memory Manager
WP-024 Retrieval Memory Foundation
WP-038 Project Readiness Assessment & Gap Classification
```

## 路由

```text
PRIVATE → USER_CLARIFICATION
EXPLICITNESS → MODEL_PROPOSAL / USER_CONFIRM
DOMAIN → DOMAIN_RESEARCH
TECHNICAL → TECHNICAL_RESEARCH
PROCESS → PROJECT_METHOD_RESEARCH
CAPABILITY → PROVIDER / PLUGIN / WORKER CAPABILITY RESOLUTION
```

## Research-on-Gap

创建新 Web Research 前必须检查：

```text
Method Assets
Procedure Model
Project Memory
Installed Skills
Existing Research
```

## Gate

- [ ] Skill=0 不再自动触发 Method Research。
- [ ] 用户方法足够时可以完全不联网。
- [ ] PRIVATE Gap 不会去 Web Search。
- [ ] Research 不覆盖用户 MANDATORY 流程。
