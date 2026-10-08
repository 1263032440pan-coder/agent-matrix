# WP-018 Desktop UI

> 状态：DONE（2026-10-07，阶段 7 收官站——UI 完整化）  
> Gate：PASS（依据 vitest 9 用例 + dashboard API 集成测试）

## 必读全局文档

```text
00-global/00-项目总览.md
00-global/05-开发约束与Agent规则.md
20-tracking/RTM-需求追踪矩阵.md
```


## 1. 任务目标

实现 Project Overview、Task Graph、Worker、Execution、Automation、Artifact、Delivery 页面

## 2. 前置依赖

- WP-003
- WP-006
- WP-011
- WP-013
- WP-016

## 3. 依赖文档

- `10-work-packages/WP-003-*.md`
- `10-work-packages/WP-006-*.md`
- `10-work-packages/WP-011-*.md`
- `10-work-packages/WP-013-*.md`
- `10-work-packages/WP-016-*.md`

## 4. 允许修改范围

- `apps/desktop/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## 页面

```text
Project Home
Project Bootstrap
Project Overview
Task Graph
Current Frontier
Worker Runtime
Execution Timeline
Artifact
Decision / Issue
Automation Control / Manual Actions
Delivery Contract
Project Settings
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

- [x] 可创建/导入/恢复项目（创建/导入 D18 既有；恢复 = Checkpoint 面板「恢复为新副本」按钮）
- [x] Task Graph 可视化（SVG 分层布局 + 依赖折线，D18 既有）
- [x] Current Frontier 高亮（READY 节点金色虚线外框 frontier-glow + frontierIds 导出）
- [x] 事件实时更新（Runtime Events 轮询 D18 既有）
- [x] Automation Mode、Manual Actions 和 Delivery 可操作/查看（mode 切换既有 + 本批 ManualActionsPanel 确认/拒绝按钮（接异步执行链）+ DeliveryPanel 契约条目 + CheckpointPanel 快照/恢复）

## 本批交付（UI 完整化 2026-10-07）

- `ProjectPanels.tsx`: Manual Actions（确认→CONFIRMED+异步 Execution 提交 / 拒绝→REJECTED 任务留 READY）+ Delivery Contract（契约版本/条目状态）+ Checkpoints（创建快照/恢复为新副本）
- dashboard API 补三段: manual_actions / delivery / frontier + confirm/reject 双端点（非 PENDING 409）
- Task Graph READY 节点 frontier-glow 高亮
- vitest 9 用例（确认交互/契约渲染/快照按钮/frontierIds）+ 后端集成测试（三字段+双端点 409）

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


## Extensions UI

Desktop 增加：

```text
Extensions
├── Capabilities
└── Runtime Extensions
```

Capability 页面显示：

```text
Plugin
Provider Type
Capabilities
Health
Permissions
Enabled
```

Runtime Extension 页面额外显示：

```text
Extension Point
Runtime Type
Permission Level
Can Pause?
Can Pause / Need User Input?
Fail Policy
Execution Order
```

用户必须能够 enable / disable 插件并查看健康状态。


## Automation UI

Project Settings 中自动化模式只允许：

```text
全自动 FULL_AUTO
全人工 MANUAL
```

MANUAL 模式提供 `Manual Actions` 队列；FULL_AUTO 不显示风险审批队列。


## Performance / Resource UI

Settings 增加：

```text
Resource Profile
○ LOW
○ STANDARD
○ HIGH
○ CUSTOM

Max concurrent workers
Max browser contexts
Heavy task exclusive
Worker idle timeout
```

同时显示：

```text
CPU Load
Available Memory
Running Workers
Waiting for Resource Tasks
```

界面必须明确提示：资源 Profile 不改变 FULL_AUTO / MANUAL。

## Demo V0.1 UI 优先级

首版只需要一个 Project Runtime Dashboard。

必须展示：

```text
Project Status
Method Readiness
Gap Count
Project Playbook
Task Graph
Current Task
Worker Status
Artifact
Gate Result
Execution Timeline
```

要求每完成一个后端能力，都能在 UI 中观察到对应状态。
