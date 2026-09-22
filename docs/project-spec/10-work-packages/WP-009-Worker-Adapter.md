# WP-009 Worker Adapter

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

实现 WorkerRuntimeAdapter、ACP Adapter、goose Worker 生命周期和事件流

## 2. 前置依赖

- WP-001
- WP-008

## 3. 依赖文档

- `10-work-packages/WP-001-*.md`
- `10-work-packages/WP-008-*.md`

## 4. 允许修改范围

- `worker_adapter/base/`
- `worker_adapter/acp/`
- `project_runtime/worker_manager/`

## 5. 禁止修改范围

除非本 WP 明确要求，否则禁止修改：

- 上游架构原则。
- 已冻结技术栈。
- 不属于本 WP 的公共接口。
- 不属于本 WP 的状态枚举。
- 其他 Work Package 的实现。
- 需求、架构、RTM 中的事实定义。

如发现必须修改，提交 Change Request。


## 统一接口

```text
createWorker()
initializeWorker()
assignWorkPackage()
sendContext()
resumeTask()
cancelTask()
getWorkerState()
streamEvents()
getArtifacts()
requestCheckpoint()
shutdownWorker()
destroyWorker()
```

V0.1：

```text
ACP Adapter
→ goose Headless Worker
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

- [ ] 统一 Adapter 接口可用
- [ ] 可创建 goose Worker Session
- [ ] 可下发 Work Package
- [ ] 可取消/恢复
- [ ] 可接收实时事件

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


## Worker Resource Profile

每个 Worker Adapter 应暴露默认资源画像：

```text
resource_profile() -> WorkerResourceProfile
```

至少包含内存估计、CPU 权重、浏览器要求、最大实例数和生命周期建议。

## V1.12 SkillExecutionContext

Worker Work Package 可选增加：

```text
resolved_skill_ref
skill_execution_context_ref
```

调用关系：

```text
Skill Resolver
→ Skill Loader
→ SkillExecutionContext
→ WorkerRuntimeAdapter.assignWorkPackage(...)
```

Worker Runtime 与 Skill 解耦：

```text
同一 Worker Runtime
+ 不同外部 TASK_EXECUTION Skill
→ 可承担不同类型任务
```

Adapter 不负责：

```text
Skill Import
Registry
Validation
Resolution
```

Worker Result 在使用 Skill 时记录：

```text
actual_skill_id
actual_skill_version
actual_skill_content_hash
```
