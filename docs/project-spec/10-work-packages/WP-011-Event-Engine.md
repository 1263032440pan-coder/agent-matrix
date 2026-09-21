# WP-011 Event Engine

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

实现 EventBus、SQLite 事件持久化和 WebSocket 推送

## 2. 前置依赖

- WP-001
- WP-002

## 3. 依赖文档

- `10-work-packages/WP-001-*.md`
- `10-work-packages/WP-002-*.md`

## 4. 允许修改范围

- `project_runtime/event/`
- `apps/runtime/api/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## 事件要求

事件必须包含：

```text
id
project_id
task_id?
worker_id?
type
payload
created_at
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

- [ ] 事件可发布/订阅
- [ ] 事件持久化
- [ ] Desktop 可通过 WebSocket 收到事件
- [ ] 核心事件 schema 校验

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


## Extension Runtime 边界

Event Bus 属于 Stable Kernel。

插件只能通过：

```text
event.observers
```

订阅事件。

Observer 不允许：

```text
阻断 Event Publish
修改 Event
修改 Project State
```

Plugin Observer 失败默认 fail_open，并生成 EXTENSION_INVOCATION_FAILED。
