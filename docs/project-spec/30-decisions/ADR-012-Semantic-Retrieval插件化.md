# ADR-012 Semantic Retrieval 插件化

## 状态

ACCEPTED

## 决策

向量语义检索不属于核心依赖。

未来通过：

```text
RetrievalProvider
→ SemanticRetrievalProvider Plugin
```

接入。

插件可以自由选择 Embedding、Vector Store、Reranker 或远程服务。

## 原因

避免核心桌面应用被重型 AI 检索基础设施绑定，同时保留高级语义搜索扩展能力。
