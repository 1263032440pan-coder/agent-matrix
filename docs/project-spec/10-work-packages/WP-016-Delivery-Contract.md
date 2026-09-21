# WP-016 Delivery Contract

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

实现交付契约、Delivery Item、缺失项检查和 Final Gate 前置能力

## 2. 前置依赖

```text
WP-002 Project State
WP-012 Artifact Manager
WP-014 Gate
WP-015 Replanner
WP-022 Traceability Graph
```

## 3. 依赖文档

- `10-work-packages/WP-002-*.md`
- `10-work-packages/WP-012-*.md`
- `10-work-packages/WP-014-*.md`
- `10-work-packages/WP-015-*.md`

## 4. 允许修改范围

- `project_runtime/delivery_contract/`
- `project_runtime/completion/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## Project Completion 前置公式

```text
Project Completed
=
Delivery Contract 满足
+
Required Task ACCEPTED
+
Critical Issue = 0
+
Final Gate PASS
+
Pending ManualAction = 0
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

- [ ] 支持 Contract 版本
- [ ] 支持 required item
- [ ] 可映射 Artifact
- [ ] 可输出 missing items
- [ ] 重大变更进入 Change；MANUAL 模式下应用变更前需要 ManualAction 确认

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


## 长项目完成约束

Delivery Item 的 SATISFIED 状态最终必须由：

```text
Completion Evidence
+
Traceability Coverage
+
Gate
```

共同支持。

Delivery Contract 不允许仅依据 Planner 或 Reviewer 的自然语言结论标记完成。
