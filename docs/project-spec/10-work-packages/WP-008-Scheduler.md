# WP-008 Scheduler

> 状态：TODO  
> 开发方式：单 Work Package 独立开发  
> Gate：PASS / FAIL / BLOCKED

## 必读全局文档

```text
00-global/00-项目总览.md
00-global/05-开发约束与Agent规则.md
20-tracking/RTM-需求追踪矩阵.md
```


## 1. 任务目标

实现调度循环、READY 判断、并行控制和 Worker Routing 入口

## 2. 前置依赖

- WP-005
- WP-006
- WP-007

## 3. 依赖文档

- `10-work-packages/WP-005-*.md`
- `10-work-packages/WP-006-*.md`
- `10-work-packages/WP-007-*.md`

## 4. 允许修改范围

- `project_runtime/scheduler/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## 调度条件

```text
Task Contract 完整
AND
HARD Dependencies 满足
AND
Artifact 可用
AND
Worker Capability 可用
AND
无资源冲突
AND
Automation Mode Controller 允许推进
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

- [ ] 只调度合法 READY Task
- [ ] 支持并发上限
- [ ] 无 Worker 时保持 Task 不丢失
- [ ] Scheduler 不执行 Task 内部逻辑

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


## Runtime Hook

任务下发前支持：

```text
scheduler.before_dispatch
```

以及：

```text
worker.before_execute
```

Runtime Hook 只能返回 Control Result / Proposal。

Scheduler 最终是否下发由 Core 决定。


## Automation Mode

```text
FULL_AUTO → READY 后可自动 dispatch
MANUAL    → READY 后创建 ManualAction，人工确认后 dispatch
```

Scheduler 不做风险分级审批。


## Resource Governor

Scheduler 的候选任务在 Dispatch 前必须经过：

```text
ResourceGovernor.evaluate(task, worker_profile, snapshot)
```

结果：

```text
ALLOW_DISPATCH
DEFER_RESOURCE
RUN_EXCLUSIVE
REDUCE_PARALLELISM
```

`DEFER_RESOURCE` 不改变 Task 的业务完成状态。
