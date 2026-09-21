# WP-021 Hierarchical Summary

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] Summary 有来源引用。
- [ ] Source 变化会使 Summary STALE。
- [ ] 只更新受影响 Scope。
- [ ] Summary 不作为事实源。
