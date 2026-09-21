# ADR-023 Pipeline 只开放稳定 Extension Point

## 状态

ACCEPTED

## 决策

Runtime Extension 只能注册到文档化、版本化的 Extension Point。

新增 Extension Point 必须经过 Change Request / ADR。

## 原因

避免“为了插件化而到处打 Hook”，保持 Core Contract 可维护。
