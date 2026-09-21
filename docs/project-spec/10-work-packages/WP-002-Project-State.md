# WP-002 Project State

> 状态：TODO  
> 开发方式：单 Work Package 独立开发  
> Gate：PASS / FAIL / BLOCKED

## 必读全局文档

```text
00-global/00-项目总览.md
00-global/03-核心领域模型.md
00-global/04-全局接口与事件规范.md
00-global/05-开发约束与Agent规则.md
20-tracking/RTM-需求追踪矩阵.md
```


## 1. 任务目标

实现核心领域实体、数据库表、Repository、迁移，以及 Project/Task 显式有限状态机的持久化基础与统一状态转移服务。

## 2. 前置依赖

- WP-001

## 3. 依赖文档

- `10-work-packages/WP-001-*.md`

## 4. 允许修改范围

- `project_runtime/project_state/`
- `infrastructure/database/`
- `infrastructure/repository/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## Project / Task 状态机实现约束

状态枚举、状态语义与合法转移表以：

```text
00-global/03-核心领域模型.md
```

为唯一事实源。WP-002 **不得自行新增、删除、重命名状态或改变合法转移关系**。如实现发现规范不足，必须提交 Change Request。

必须提供统一 Core 状态转移入口，例如：

```text
StateTransitionService
├── transition_project(command)
└── transition_task(command)
```

状态修改必须遵循：

```text
Command / Proposal
→ Transition Validation
→ State Transaction
→ State Mutation
→ Event Persist
→ Commit
```

禁止业务模块直接执行等价于：

```text
task.status = ...
project.status = ...
repository.save(...)
```

的绕过状态机写入。

## 核心实体

至少实现：

```text
Project
Task
TaskDependency
TaskRun
WorkerRuntime
WorkerInstance
Artifact
Issue
Decision
Constraint
GateResult
ManualAction
HumanDecision
Event
Checkpoint
DeliveryContract
DeliveryItem
```


## 6. 开发输出

必须输出：

```text
代码
单元测试
必要集成测试
接口/Schema
日志与错误处理
变更文件清单
测试结果
```

## 7. 验收条件

- [ ] Project/Task/Dependency/Artifact/Issue/Decision/Constraint 可持久化
- [ ] Alembic migration 可重复执行
- [ ] Repository 单测通过
- [ ] `ProjectStatus` / `TaskStatus` 枚举与 `03-核心领域模型.md` 完全一致
- [ ] 实现统一 `StateTransitionService`（或等价 Core FSM Service）
- [ ] 合法转移可成功持久化
- [ ] 非法转移返回 `INVALID_STATE_TRANSITION` 且数据库状态保持不变
- [ ] 状态变更与对应 Event 在同一事务边界提交
- [ ] `ACCEPTED` / `COMPLETED` / `CANCELLED` 终态不可被静默回退
- [ ] 完成 `40-testing/D03-状态机测试矩阵.md` 中适用于 WP-002 的测试

## 8. Gate

### PASS

全部验收条件满足，测试通过，无 BLOCKED。

### FAIL

存在实现错误、测试失败或不符合契约。

### BLOCKED

依赖设计、外部能力、前置 WP 或架构问题导致无法继续。

## 9. 完成后 RTM 更新

仅当 Gate = PASS 时：

```text
20-tracking/RTM-需求追踪矩阵.md
```

中本 WP 对应开发状态可更新为 `DONE`。

不得由 Coding Agent 修改需求/架构列的事实内容。
