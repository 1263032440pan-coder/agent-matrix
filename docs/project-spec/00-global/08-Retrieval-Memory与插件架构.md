# 08 Retrieval Memory 与插件架构

## 核心定位

Retrieval Memory 是五类 Project Memory 之一。

它不是一个具体搜索引擎，而是：

```text
项目内容统一检索层
```

## 架构

```text
Context Builder
→ Project Memory Manager
→ Retrieval Memory
→ Retrieval Orchestrator
→ retrieval.providers
```

Provider：

```text
FTS5RetrievalProvider [Core]
SemanticRetrievalProvider [Optional Plugin]
RemoteRetrievalProvider [Optional Plugin]
GraphRetrievalProvider [Future]
```

## Core Responsibilities

Retrieval Memory Core 负责：

```text
RetrievalDocument
Source / Version
Index Lifecycle
Project Scope
Metadata Filter
Result Normalize
Deduplicate
Fusion
Provider Routing
```

Provider 只负责检索算法。

## Semantic Retrieval

Semantic Retrieval 不是 Semantic Memory，也不是第六类 Memory。

它是：

```text
Capability Extension
type = capability
point = retrieval.providers
```

可包含：

```text
Chunk
Embedding
Vector Index
Semantic Search
Rerank
```

## 多 Provider

未来可采用：

```text
LEXICAL
SEMANTIC
HYBRID
```

HYBRID：

```text
FTS5 Top-K
+
Semantic Top-K
→ Normalize
→ Deduplicate
→ Rank Fusion
→ Optional Rerank
→ Final Top-N
```

## 生命周期一致性

所有 Provider 必须使用统一：

```text
source_id
source_version
content_hash
is_active
```

Artifact 更新时 Retrieval Memory Core 发出统一索引更新，不允许 FTS5 与 Vector 使用不同版本。
