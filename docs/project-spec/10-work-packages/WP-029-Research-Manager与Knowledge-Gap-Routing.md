# WP-029 Research Manager 与 Gap Resolution

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_gap_resolution.py 9 用例）

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

- [x] Skill=0 不再自动触发 Method Research（MANDATORY 方法资产在 + Skill=0 → 不建 Research；Skill 数量不是触发变量——方法足够性才是）
- [x] 用户方法足够时可以完全不联网（五查命中 → RESOLVED_LOCALLY + 证据链 notes 落库：method_assets/procedure_model 各 True；不建任何 ResearchRecord）
- [x] PRIVATE Gap 不会去 Web Search（硬路由 USER_CLARIFICATION_REQUESTED + ResearchRecord 零创建实测）
- [x] Research 不覆盖用户 MANDATORY 流程（gap 挂 MANDATORY 资产节点 → 覆盖路由为 USER_CLARIFICATION，source=mandatory-authority，零 Research）

## 实现说明

- `research/models.py`: ResearchRecord——可复用研究资产（(question, research_kind) 唯一约束 = Existing Research 检查的物化；ANSWERED/SUPERSEDED 生命周期）
- `research/router.py`: GapResolver 六类型路由表 + Research-on-Gap 五查（Method Assets 权威级/Procedure Model 节点实质值——模型存在≠能回答缺口/Project Memory 统一门面检索/Installed Skills ENABLED/Existing Research 同题）+ 五查全 miss 才建新（幂等同题复用）
- 红线: PRIVATE 永不联网；MANDATORY 资产上的 gap 只进澄清不改写流程；CAPABILITY 走注册解析非 Web Research
- 结构性断言: router 无 httpx/aiohttp/litellm 执行路径（只路由不执行——research 执行属 research.providers 扩展）
- migration c4d9f2a8e6b1
- 顺手修复 D06 遗留映射错误: 「验收」关键词原误映射 gate 字段（应为 acceptance_criteria；gate=放行检查点）——test_procedure READY 用例语义同步修正
