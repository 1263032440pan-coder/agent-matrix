# WP-024 Retrieval Memory Foundation

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 任务目标

实现核心版轻量 Retrieval Memory。

## 前置依赖

```text
WP-001
WP-002
WP-003
WP-004
```

## 固定技术

```text
SQLite FTS5
BM25
SQL Metadata Filter
Path / Filename Search
ripgrep
```

## 模块

```text
RetrievalDocumentRepository
FTS5Indexer
FTS5Retriever
PathRetriever
RipgrepRetriever
RetrievalMerger
```

## 索引生命周期

```text
Source Created → Index
Source Updated → Replace / Update
Source Deleted → Deactivate
content_hash unchanged → Skip
```

## 验收

- [ ] 文本 Artifact 可以进入 FTS5。
- [ ] 支持 project_id 隔离。
- [ ] 支持 Metadata Filter。
- [ ] 支持 BM25 排序。
- [ ] 支持文件名 / 路径搜索。
- [ ] 支持 ripgrep 代码搜索。
- [ ] 相同 content_hash 不重复索引。
- [ ] Artifact 更新后旧内容不再作为 ACTIVE 结果。
- [ ] 不引入 Embedding / Rerank / Vector DB。
