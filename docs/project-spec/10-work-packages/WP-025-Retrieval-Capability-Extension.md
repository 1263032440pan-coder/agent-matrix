# WP-025 Retrieval Capability Extension

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

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

- [ ] FTS5 通过统一 Provider Contract 注册。
- [ ] 新 Provider 不需要修改 Retrieval Memory Core。
- [ ] Provider 可启停。
- [ ] 多 Provider 结果可 Normalize / Deduplicate / Fusion。
