# WP-037 Project Method Asset Discovery 与 Procedure Normalization

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_method_discovery.py 9 用例 + 既有 procedure 链 75 用例零回归）

## 目标

发现用户已有项目方法资产，并将其转换为 Project Procedure Model。

## 前置依赖

```text
WP-004 State Discovery
WP-020 Project Memory Manager
WP-026 Project Understanding
```

## Method Asset 类型

```text
PROCESS
SOP
STANDARD
TEMPLATE
HISTORICAL_REFERENCE
ACCEPTANCE_RULE
ROLE_DEFINITION
DELIVERY_RULE
```

## Procedure Node

至少抽取：

```text
input
activity
output
role
dependency
gate
acceptance
evidence
constraint
rework_path
provenance
```

无法确认的字段必须保留 UNKNOWN / AMBIGUOUS，不允许脑补成事实。

## Extension Points

```text
method.assets.providers
method.assets.processors
procedure.normalizers
```

## Gate

- [x] 能从多个文档中发现流程资产（SOP/模板/历史参考三文档实测分类正确；WP-026 hints 先验 + 内容复核双路径，无 understanding 快照也能发现）
- [x] 能建立 Procedure Model（ProcedureNormalizer 既有链——多资产 → 节点 + model）
- [x] 每个节点可追溯 source_ref（node.source_ref + provenance.asset_id 双层追溯，实测）
- [x] 未明确 Acceptance 时保持 UNKNOWN（无验收标记文档 → 全节点 acceptance_criteria == "UNKNOWN"，专项反脑补断言）
- [x] 冲突流程可标记 CONFLICTED（同名节点字段值分歧 → CONFLICTED，实测）

## 实现说明

- `project_runtime/procedure/discovery.py`: MethodAssetDiscovery（多文档发现 + WP-026 五信号→八类 AssetType 映射 + 内容信号复核 + meta enrichment 落库——authority 用户声明程序不覆盖）
- Extension 三点接入（CR-METHOD-POINTS 登记）: method.assets.providers / method.assets.processors 在 discovery 链上真实 fire（advisory，MIDDLEWARE/ENRICH）；procedure.normalizers 点位登记（Normalizer extractor 可换——KeywordExtractor/LLMExtractor 已是 Protocol 形态）
- STABLE_POINTS 21→24（14 文档 V1.9 清单落地三点；其余四点随 WP-038/029/030 到位时登记）
- 零脑补红线结构性断言延续（无 Skill 写路径）
