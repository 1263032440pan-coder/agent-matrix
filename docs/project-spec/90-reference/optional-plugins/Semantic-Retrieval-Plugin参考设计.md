# Semantic Retrieval Provider Plugin 参考设计

> 可选插件，不属于核心 V1 完成条件。

## 插件类型

```text
Plugin Package
→ Capability Extension
→ retrieval.providers
```

不是：

```text
Semantic Memory
```

## Manifest

```yaml
plugin:
  id: semantic-retrieval
  version: 1.0.0
  api_version: 1

runtime:
  transport: stdio
  command:
    - semantic-retrieval-plugin.exe

permissions:
  - read_retrieval_document
  - local_index

extensions:
  - id: semantic-retrieval-provider
    type: capability
    point: retrieval.providers
    handler: semantic_retrieval
    priority: 100
```

## 插件职责

```text
Chunk
Embedding
Vector Index
Semantic Search
Optional Rerank
```

## 不负责

```text
Project State
Artifact Version
Task Graph
RetrievalDocument Lifecycle
Gate
Completion
```

这些仍由 Core 负责。
