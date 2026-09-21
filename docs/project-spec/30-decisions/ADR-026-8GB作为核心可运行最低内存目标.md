# ADR-026 8GB 作为核心可运行最低内存目标

## 状态

ACCEPTED

## 决策

V1 核心产品设计目标：

```text
8GB RAM 可以运行
```

默认采用 LOW Resource Profile：

```text
Worker concurrency = 1
Browser contexts = 1
Heavy task exclusive
Local LLM / Semantic Retrieval 默认关闭
Worker / Plugin 按需启动
```

## 不包含

本地大模型、大型 Embedding/Rerank、重型向量数据库等可选插件的最低配置另行定义。
