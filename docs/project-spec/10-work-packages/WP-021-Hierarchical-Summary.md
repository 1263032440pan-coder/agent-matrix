# WP-021 Hierarchical Summary

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_hierarchical_summary.py 5 用例）

## 前置依赖

```text
WP-011 Event Engine
WP-012 Artifact Manager
WP-020 Project Memory Manager
```

## 任务目标

实现 Project / Workstream / Module / Task 的可追溯分层摘要。

## Summary

```text
summary
key_facts
key_decisions
open_issues
task_refs
artifact_refs
evidence_refs
source_version
status
```

## 触发

```text
Artifact Update
Decision
Issue Change
Task State Change
Checkpoint（后续接入）
```

## 验收

- [x] Summary 有来源引用（task_refs/artifact_refs/evidence_refs + source_version 水位；PROJECT 摘要引用全部任务与产物、TASK 摘要引用自身产物，实测）
- [x] Source 变化会使 Summary STALE（mark_stale：task 级变化→该 TASK + PROJECT 祖先链 STALE、兄弟 scope 不动；refresh 后 FRESH 且 source_version 前进）
- [x] 只更新受影响 Scope（t1 变化后 refresh 只重建 t1+PROJECT——t2 摘要内容/状态/版本三不动，实测）
- [x] Summary 不作为事实源（结构性断言：service 源码禁 FSM 写/状态 INSERT/UPDATE/DELETE——唯一写路径是 ProjectSummary 自身表；摘要为派生视图，refresh 从 MemoryManager 读活状态）

实现：memory_summary/（models: ProjectSummary 四层 scope+九字段全量 / service: SummaryService(mark_stale/refresh_scope/stale_scopes/watermark) + SummaryGenerator(PROJECT/TASK 确定性摘要)）
触发侧：mark_stale 对应四触发（Artifact Update/Decision/Issue Change/Task State Change——Checkpoint 按 WP 说明后续接入）；WP-020 get_summary_memory 已接线返回真实数据（test_wp20_summary_memory_wiring）
