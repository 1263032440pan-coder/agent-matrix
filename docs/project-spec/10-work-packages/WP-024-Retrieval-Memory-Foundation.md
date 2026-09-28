# WP-024 Retrieval Memory Foundation

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_retrieval_memory.py 10 用例，九条验收全覆盖，真实 rg 子进程）

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

- [x] 文本 Artifact 可以进入 FTS5（RetrievalDocumentRepository.upsert + FTS5Indexer；CJK bigram 预分词——unicode61 无中文分词，整句成单 token 不可查，索引/查询两侧对称 bigram 化，仍纯 FTS5）
- [x] 支持 project_id 隔离（SQL WHERE 强制；跨项目不可见、无 scope 见全部）
- [x] 支持 Metadata Filter（doc_type SQL 过滤）
- [x] 支持 BM25 排序（FTS5 内置 bm25()，密集命中文档排前且 score 单调）
- [x] 支持文件名 / 路径搜索（PathRetriever：path/title LIKE）
- [x] 支持 ripgrep 代码搜索（真实 rg 15.2 子进程；Windows 盘符冒号路径解析修复：按 :<digits>: 锚定切分）
- [x] 相同 content_hash 不重复索引（upsert skip 返回 None + ACTIVE 部分唯一索引 uq_retdoc_same_content 双保险）
- [x] Artifact 更新后旧内容不再作为 ACTIVE 结果（版本化：旧 doc active=0、FTS 行删除；旧内容查询为空、新版本可查）
- [x] 不引入 Embedding / Rerank / Vector DB（结构性断言 test_no_vector_tech：源码 banned 词扫描）

实现：project_runtime/retrieval/（models/repository/fts/retrievers/merger 六模块）+ migration d9f3c2e8b7a4
Merger：跨 provider 归一化 + (source,source_id,version) 去重（最优分保留）+ rg 行级去重 + FTS5→path→ripgrep 融合序
