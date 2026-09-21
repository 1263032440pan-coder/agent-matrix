# AGENTS.md

> 本文件为项目级 Agent 指令。所有在此仓库工作的 AI 编码 Agent 必须遵守。

## 项目概述

Project Autonomous Agent System — 通用本地单用户桌面应用，核心是 Microkernel + AOP/Extension Runtime 架构。

## 关键约束

1. **Stable Kernel 不可被插件直接修改**：Project State / Task Graph / Artifact Lifecycle / Event Bus / Gate / Checkpoint / Transaction
2. **自动化模式只允许 FULL_AUTO / MANUAL**，不引入第三种模式
3. **插件只允许 READ_ONLY / ENRICH / VALIDATE / CONTROL 权限**，禁止 DIRECT_STATE_MUTATE
4. **所有编码修改必须通过 Proposal → Core Validation → State Transaction 路径**
5. **Extension Point 必须稳定 ID**，不得私自增加未登记的 Hook 点
6. **Resource Governor 与 Automation Mode 正交**，不可混淆

## 技术栈

- Desktop: Tauri 2 + React 19 + TypeScript 5 + Ant Design 6
- Runtime: Python 3.12 + FastAPI + SQLite + Pydantic v2
- AI: LiteLLM
- 检索: SQLite FTS5 + BM25（核心），Embedding/Vector 为可选插件

## 开发规则

- Demo V0.1 阶段可简化实现，但不可破坏正式架构边界
- 新 Extension Point 需要 Change Request → ADR
- 参考文档：`docs/project-spec/`