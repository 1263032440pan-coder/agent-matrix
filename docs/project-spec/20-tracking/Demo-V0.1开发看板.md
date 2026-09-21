# Demo V0.1 开发看板

| ID | 模块 | 状态 | Demo 必须 | 备注 |
|---|---|---|---|---|
| D01 | 工程骨架 | TODO | 是 | Runtime + Desktop |
| D02 | Project/Task/Artifact/Event | TODO | 是 | Microkernel 最小模型 |
| D03 | Project State / Task FSM | TODO | 是 | 前置：03 中 FSM 已冻结；验收：D03 状态机测试矩阵；EXECUTION_DONE ≠ ACCEPTED |
| D04 | Task Graph | TODO | 是 | Dependency + Cycle Check |
| D05 | Method Asset Import | TODO | 是 | md/txt/粘贴文本即可 |
| D06 | Procedure Normalization | TODO | 是 | LLM → Structured JSON |
| D07 | Method Readiness | TODO | 是 | READY/PARTIAL/AMBIGUOUS/MISSING |
| D08 | Gap Classification | TODO | 是 | Private/Method/Domain/Technical/Process |
| D09 | Project Playbook | TODO | 是 | 保留 Provenance |
| D10 | Dynamic Planner | TODO | 是 | Playbook → Task Graph |
| D11 | Worker Adapter + 1 Worker | TODO | 是 | 只接一个真实 Worker |
| D12 | Artifact 回传 | TODO | 是 | 文件真实落盘 |
| D13 | Gate | TODO | 是 | 3 个基础 Validator |
| D14 | Scheduler | TODO | 是 | READY → Dispatch |
| D15 | Resource Governor Lite | TODO | 是 | max_workers=1 |
| D16 | Minimal Extension Registry | TODO | 是 | in-process 即可 |
| D17 | Desktop UI | TODO | 是 | 单 Project Dashboard |
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
