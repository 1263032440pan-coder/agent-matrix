# WP-020 Project Memory Manager

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_project_memory.py 4 用例）

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

- [x] 五类 Memory 有统一入口（ProjectMemoryManager：get_state_memory / get_artifact_memory / get_summary_memory / retrieve / get_execution_memory / get_memory_version 六接口齐）
- [x] Retrieval 只依赖 RetrievalProvider（manager 只持 registry 契约经 RetrievalOrchestrator 路由——结构性断言：manager 源码无 FTS5Retrieval 直连）
- [x] Worker Session 退出不影响 Memory（每次调用独立短事务 session + engine 注入化；worker session 关闭后全部 memory 接口照常，实测）
- [x] Event / Artifact 可作为 Memory Source（execution memory 读 Event Store 实测；artifact memory 读最新版本视图）

实现：project_runtime/memory/manager.py
Summary Memory：WP-021 实体落地前返回空（registry-ready，不伪造）；Checkpoint 按 WP 说明为后续 Execution Memory 附加来源
