# ADR-011 核心检索采用 SQLite FTS5

## 状态

ACCEPTED

## 决策

核心 Retrieval Memory 使用：

```text
SQLite FTS5 + BM25
Metadata / Path Search
ripgrep
```

不强制引入 Embedding、Rerank、Vector DB。

## 原因

产品是本地单用户桌面应用，优先保证安装轻量、低资源、低运维和可离线运行。
