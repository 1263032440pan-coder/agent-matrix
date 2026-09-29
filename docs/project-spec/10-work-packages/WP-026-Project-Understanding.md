# WP-026 Project Understanding

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_project_understanding.py 10 用例）

## 目标

从用户目标和已有证据识别：

```text
Domain Hypothesis
Project Type Hypothesis
Goal
Known Facts
Known Requirements
Assumptions
Open Questions
Knowledge Gaps
Existing Method Asset Hints
```

## 关键变化

Project Understanding 不负责假设“用户没有方法”。

应优先识别：

```text
是否存在流程 / SOP / 实施方案 / 验收材料 / 历史模板
```

并交给 WP-037。

## 验收

- [x] Fact / Assumption / Open Question 分离（三分桶互斥有专项断言：同一陈述不得出现在两个桶；待定/未明确标记必进 OpenQuestion 绝不静默假设；Assumption 明标"推断，非事实"；Requirement 类陈述同时是 Fact（有材料依据））
- [x] 能识别潜在 Method Asset（五信号 PROCESS/SOP/PLAN/ACCEPTANCE/TEMPLATE + provenance=user-material + confidence + rationale；含 asset_id 指向，交 WP-037 评估）
- [x] 不把普通项目文档直接当 Skill（结构性断言：模块源码无 SkillDefinition/skills.importer/SkillLoader/bind_skill 任何写路径——唯一写实体 ProjectUnderstanding）

## 实现说明

- `project_runtime/understanding/`（models: ProjectUnderstanding 八字段快照 / analyzer: DeterministicAnalyzer + UnderstandingService）
- `UnderstandingAnalyzer` Protocol 为 WP-037 LLM 版预留换入点（测试验证协议可注入替换）
- API：POST/GET `/api/projects/{id}/understanding`（理解快照产/读——Planning Context 消费入口，WP-030 Playbook 从 GET 取）
- 零 Archetype（SYS-032）：域识别只产 HYPOTHESIS（UNKNOWN 诚实标注+assumption），无任何行业预设模板（结构性断言）
- 与 D05-D09 关系：认知链上游新站点——不接管 Normalizer/Readiness/Gap/Planner（结构性断言）
- migration b3c8e1f5a9d7；env.py 模型注册排序修正（I001）
