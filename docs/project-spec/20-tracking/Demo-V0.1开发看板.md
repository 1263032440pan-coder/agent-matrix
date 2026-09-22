# Demo V0.1 开发看板

| ID | 模块 | 状态 | Demo 必须 | 备注 |
|---|---|---|---|---|
| D01 | 工程骨架 | PASS | 是 | Tauri 2 + FastAPI + SQLite WAL + Alembic + vitest + pytest |
| D02 | Project/Task/Artifact/Event | PASS | 是 | 17 实体 + migration + Repository + 24 tests |
| D03 | Project State / Task FSM | PASS | 是 | FSM 115 tests + Ruff 0 errors |
| D04 | Task Graph | PASS | 是 | 32 tests — CRUD + cycle detection + HARD dep only ACCEPTED |
| D05 | Method Asset Import | PASS | 是 | 18 tests — file/text import + type classify + SHA256 dedup |
| D06 | Procedure Normalization | PASS | 是 | V0.1 关键词抽取（11字段+UNKNOWN+溯源），LLMExtractor接口预留 |
| D07 | Method Readiness | PASS | 是 | 6-dim assess + Gap生成 + 9 tests |
| D08 | Gap Classification | PASS | 是 | V0.1 维度映射产出 2/6 类（EXPLICITNESS/PROCESS）+ 6类路由框架，四类待 LLM 阶段 |
| D09 | Project Playbook | PASS | 是 | V0.1 用户方法为主体 + Provenance + 8 tests |
| D10 | Dynamic Planner | PASS | 是 | Playbook→Task Graph + Authority尊重 + 8 tests |
| D11 | Worker Adapter + 1 Worker | PASS | 是 | MockWorker V0.1 + 红线保护 + 5 tests |
| D12 | Artifact 回传 | PASS | 是 | 落盘 + SHA256 + 版本 + 管线接线 TaskRun/Artifact |
| D13 | Gate | PASS | 是 | GateCore + File 真实校验(SHA256)/Build/Test V0.1占位 + 4结果(FAIL/BLOCKED/NEED_APPROVAL均落REWORK) + actor防护(FSM强制) + 7 tests |
| D14 | Scheduler | PASS | 是 | READY→Dispatch + ResourceGovernor 集成 + FULL_AUTO/MANUAL + 7 tests |
| D15 | Resource Governor Lite | PASS | 是 | ResourceGovernor 组件 + ALLOW/DEFER_RESOURCE + 绝不FAIL + 4 tests（Profiles Demo后，WP-036 PARTIAL） |
| D16 | Minimal Extension Registry | PASS | 是 | in-process Registry + 7 CapabilityType + 4 Permission(禁DIRECT_STATE_MUTATE) + 优先级/enable + 统一异常 + 9 tests |
| D17 | Desktop UI | PASS | 是 | Dashboard API（只读聚合）+ React Dashboard（轮询3s）+ vitest 2 + pytest 3（事件推送 WP-011 Demo 后，创建/导入 UI D18 E2E） |
| D18 | E2E Demo | TODO | 是 | 全链路真实跑通 |

## Demo 后再做

```text
Plugin RPC
Semantic Retrieval
Advanced Memory
Traceability
Checkpoint Pro
Multi Worker
Full Resource Governor
```


## D03 前置 Gate

D03 只有在以下规范均存在且无冲突时才允许进入 `DOING`：

```text
00-global/03-核心领域模型.md
00-global/04-全局接口与事件规范.md
40-testing/D03-状态机测试矩阵.md
```

Coding Agent 发现状态定义不完整时必须：

```text
创建 Change Request
→ D03 标记 BLOCKED / CHANGE_REQUIRED
→ 等上游规范修订后恢复
```
