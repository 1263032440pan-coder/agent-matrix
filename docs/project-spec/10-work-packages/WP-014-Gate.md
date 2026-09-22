# WP-014 Gate

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

实现 Hard Check、Semantic Review、Acceptance Criteria 和 Task 验收状态迁移

## 2. 前置依赖

- WP-002
- WP-012
- WP-013

## 3. 依赖文档

- `10-work-packages/WP-002-*.md`
- `10-work-packages/WP-012-*.md`
- `10-work-packages/WP-013-*.md`

## 4. 允许修改范围

- `project_runtime/reviewer/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## Gate Pipeline

```text
EXECUTION_DONE
→ Artifact Collection
→ Hard Check
→ Semantic Review
→ Acceptance Criteria
→ Gate Result
```

结果：

```text
PASS
FAIL
BLOCKED
NEED_APPROVAL
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

- [ ] EXECUTION_DONE 不直接 ACCEPTED
- [ ] 支持 PASS/FAIL/BLOCKED/NEED_APPROVAL
- [ ] 至少实现 File/Build/Test 等 Validator 接口
- [ ] Gate 结果持久化

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


## Runtime Validator 集成

Gate Core 完成基础验收后调用：

```text
gate.validators
```

Validator Extension 只返回：

```text
PASS
WARNING
FAIL
```

不得直接设置 Task Status。

Gate Core 负责合并 Validator Result 并执行最终状态转换。

## V1.12 Skill Result 边界

如果 Worker 使用外部 Skill，SkillExecutionResult 只作为：

```text
Artifact / Evidence / Execution Metadata
```

参与 Gate。

硬规则：

```text
SkillExecutionResult.status = COMPLETED
!=
Gate PASS
!=
Task ACCEPTED
```

任何 Skill 都不能通过输出某个自定义字段绕过 Gate Core。

Gate 仍然按 Task Acceptance Criteria、Artifact、Validator 和 Evidence 做最终判断。
