# WP-020 Project Memory Manager

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 任务目标

实现五类长期 Project Memory 的统一访问层。

## 前置依赖

```text
WP-002 Project State
WP-011 Event Engine
WP-012 Artifact Manager
WP-024 Retrieval Memory Foundation
WP-025 Retrieval Provider Plugin Framework
```

## 五类 Memory

```text
State
Artifact
Summary
Retrieval
Execution
```

Checkpoint 在 WP-017 完成后作为 Execution Memory 的附加来源接入，不作为本 WP 前置依赖。

## 核心接口

```text
get_state_memory()
get_artifact_memory()
get_summary_memory()
retrieve()
get_execution_memory()
get_memory_version()
```

## 验收

- [ ] 五类 Memory 有统一入口。
- [ ] Retrieval 只依赖 RetrievalProvider。
- [ ] Worker Session 退出不影响 Memory。
- [ ] Event / Artifact 可作为 Memory Source。
