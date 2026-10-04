# WP-017 Checkpoint

> 状态：DONE（2026-09-30，阶段 6 第 2 站）  
> Gate：PASS（依据 tests/test_checkpoint.py 7 用例）

## 必读全局文档

```text
00-global/00-项目总览.md
00-global/05-开发约束与Agent规则.md
20-tracking/RTM-需求追踪矩阵.md
```


## 1. 任务目标

实现项目快照、恢复和自动 Checkpoint

## 2. 前置依赖

- WP-002
- WP-005
- WP-011
- WP-016
- WP-020

## 3. 依赖文档

- `10-work-packages/WP-002-*.md`
- `10-work-packages/WP-005-*.md`
- `10-work-packages/WP-011-*.md`
- `10-work-packages/WP-016-*.md`

## 4. 允许修改范围

- `project_runtime/checkpoint/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## Checkpoint 内容

```text
Project
Task Graph
Current Frontier
Artifact Versions
Decision
Issue
ManualAction / HumanDecision
Delivery Contract
Worker References
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

- [x] 可保存 Project/Graph/Frontier/Artifact/Issue/ManualAction / HumanDecision/Delivery（八段快照 JSON 落盘 + content_hash + 元数据行——含 Research 决策段/Gaps Issue 段/ManualAction 段/Delivery 段，全段实测）
- [x] 可恢复（恢复到**新项目副本** restored- 前缀——Task 状态按快照原样重建、依赖边保留、原项目逐字不动实测）
- [x] Worker 不存在时项目状态仍可恢复（零 TaskRun/零 Execution 场景实测——快照自包含，worker_references 仅计数参考）
- [x] 关键事件自动创建（AUTO_GATE_FAIL / AUTO_MILESTONE 双触发实测——advisory best-effort 绝不打断主链）

## 实现说明

- `project_runtime/checkpoint/`（models: CheckpointRecord 元数据行 / manager: 快照八段收集 + JSON 落盘 + 恢复到新副本 + 篡改检测（content_hash 校验）+ schema 版本对齐 + 文件缺失拒绝）
- 安全恢复语义: 原项目永不覆盖（restored- 副本）；状态重建走 fixture 直建（恢复的是已验证历史状态，不走 FSM 重放）
- migration f7a3c5e9b2d8

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
