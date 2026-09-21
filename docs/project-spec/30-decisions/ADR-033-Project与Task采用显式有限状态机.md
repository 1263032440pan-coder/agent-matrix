# ADR-033 Project 与 Task 采用显式有限状态机

## 状态

```text
ACCEPTED
```

## 背景

D03 开工前发现：现有规范要求“非法状态转移必须被拒绝”，但未冻结 `TaskStatus` / `ProjectStatus` 的完整枚举和合法转移表。若由 Coding Agent 自行设计，将导致开发实现、测试验收和后续模块对状态语义产生分歧。

## 决策

Project Runtime Stable Kernel 对 Project 与 Task 均采用显式有限状态机（FSM）。

具体状态、状态语义和合法转移表的唯一事实源为：

```text
00-global/03-核心领域模型.md
```

接口、事件与错误契约由：

```text
00-global/04-全局接口与事件规范.md
```

定义。

## 核心规则

1. `EXECUTION_DONE != ACCEPTED`。
2. `RUNNING -> ACCEPTED` 永远非法；必须经过 `EXECUTION_DONE -> Gate -> ACCEPTED`。
3. HARD Dependency 只有上游 Task `ACCEPTED` 才算满足。
4. Project / Task 状态只能由 Stable Kernel 的统一 State Transition Service 提交。
5. Worker、Planner、Scheduler、Gate、Plugin、Extension、UI 只能提出 Command / Proposal 或触发条件，不得直接写状态。
6. 状态变更与对应 Event 必须在同一事务边界提交。
7. `ACCEPTED`、`COMPLETED`、`CANCELLED` 是终态；历史完成事实不得静默回退。后续变更通过新 Task / Revision / Superseding Task 表达。
8. Demo V0.1 不定义 `ProjectStatus=FAILED`；Task 失败由 `TaskStatus.FAILED` 表达，项目级无法推进由 `BLOCKED` + Issue 表达。

## 原因

- 为 Coding Agent 提供确定性实现契约。
- 为 Gate / Scheduler / Replanner / Current Frontier 提供统一状态语义。
- 避免不同模块各自发明状态或转移。
- 使非法转移可以程序化测试。
- 保证事件审计、Checkpoint 和恢复具有稳定事实基础。

## 影响

直接影响：

```text
WP-002 Project State
WP-005 Task Graph
WP-006 Current Frontier
WP-008 Scheduler
WP-014 Gate
WP-015 Replanner
WP-017 Checkpoint
WP-019 E2E
```

其中 D03 首先实现 FSM Core；后续 WP 必须复用，不得自行定义第二套状态机。

## 变更规则

未来若新增状态或修改合法转移，必须：

```text
Change Request
→ ADR / 规范评审
→ 更新 03
→ 更新 04
→ 更新状态机测试矩阵
→ 再修改代码
```

不得由 Coding Agent 在实现过程中静默扩展状态枚举。
