# WP-025 Retrieval Capability Extension

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_retrieval_providers.py 7 用例）

## 目标

将 Retrieval Memory 的检索实现统一接入：

```text
retrieval.providers
```

Extension Point。

## 前置依赖

```text
WP-024 Retrieval Memory Foundation
WP-033 Capability Extension Framework
```

## Core Provider

```text
FTS5RetrievalProvider
```

## Optional Provider

```text
SemanticRetrievalProvider
RemoteRetrievalProvider
GraphRetrievalProvider
```

## Retrieval Memory Core 负责

```text
Source / Version
Lifecycle
Scope / Metadata Filter
Result Normalize
Deduplicate
Fusion
```

## Provider 负责

```text
具体检索算法
```

## 验收

- [x] FTS5 通过统一 Provider Contract 注册（FTS5RetrievalProvider：name/capability=RETRIEVAL/permission=READ_ONLY/priority=100；经 registry.invoke 统一调用实测命中）
- [x] 新 Provider 不需要修改 Retrieval Memory Core（StubSemanticProvider 仅 register 即参与——Orchestrator 遍历 registry 零特判；Semantic/Remote/Graph 未来同路径接入）
- [x] Provider 可启停（registry.set_enabled：禁用后不参与且不再被调用、重启恢复参与）
- [x] 多 Provider 结果可 Normalize / Deduplicate / Fusion（跨 provider + provider 内 (source,source_id,version) 去重保留最优分；融合序 fts5→semantic；错误隔离——崩溃 provider 记 errors 不拖垮检索）

实现：retrieval/providers.py（FTS5RetrievalProvider + RetrievalOrchestrator）
分工兑现：Core（Orchestrator）= routing/normalize/dedup/fusion/隔离；Provider = 检索算法（FTS5RetrievalProvider 只做 search 参数化）
