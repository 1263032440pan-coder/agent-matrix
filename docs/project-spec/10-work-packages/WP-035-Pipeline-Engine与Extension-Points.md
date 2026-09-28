# WP-035 Pipeline Engine 与 Extension Points

> 状态：DONE（待检查方复验确认）  
> Gate：PASS（依据 tests/test_pipeline_engine.py 14 用例：五条验收 + 复验预告五重点全覆盖）

## 目标

把核心生命周期拆成稳定 Base Pipeline，并将 Extension Point 编译为可执行 Pipeline。

## 前置依赖

```text
WP-033 Capability Extension Framework
WP-034 Runtime Extension Framework
```

## 首批 Pipeline

```text
project_bootstrap
project_understanding
planning
task_execution
gate_evaluation
delivery_validation
```

## Task Execution

```text
task.prepare
→ automation.check
→ context.build
→ worker.select
→ worker.execute
→ result.process
→ gate.evaluate
```

## Extension 顺序

支持：

```text
priority
before
after
```

必须构建 Extension Dependency Graph 并拓扑排序。

## 冲突

```text
EXTENSION_DEPENDENCY_CYCLE
```

必须阻止编译。

## 事务原则

插件调用在 State Transaction 外执行。

## 验收

- [x] Base Pipeline 可声明（首批六条：project_bootstrap/project_understanding/planning/task_execution/gate_evaluation/delivery_validation；task_execution 七步固定序）
- [x] Hook / Middleware / Validator 可注入（三类经 ExtensionBinding 注入不同 pipeline；点必须在 pipeline 声明的稳定点子集内，越界拒绝）
- [x] 编译结果可查看（CompiledPipeline.ordered_steps/registered_extensions/dependency_graph_hash；GET /api/pipelines/{id}）
- [x] 插件 enable/disable 会触发重新 compile（bind/unbind 事务性触发重编译；hash 真实变化：增删扩展→hash 变、还原→hash 复原、无变化重编译→hash 不变——确定性内容指纹非时间戳）
- [x] 顺序冲突可检测（EXTENSION_DEPENDENCY_CYCLE：a→b→a 环抛错且 binding 事务回滚不记录；拓扑边优先于 priority，priority 处理平局）

复验预告五重点:
1. dependency_graph_hash 真实变更检测（test_bind_unbind_changes_hash + test_recompile_same_state_same_hash）
2. 扩展点只落 21 稳定点（definition.validate 强制子集；共用 WP-034 STABLE_POINTS 单一来源）
3. before/after 执行顺序（test_execution_order_before_after_interleaving：核心步骤基序 + 扩展围绕）
4. 核心步骤失败 vs 扩展失败（test_core_failure_vs_extension_failure：扩展炸→记录不阻塞 COMPLETED；核心炸→FAILED + failed_step + 后续步骤不执行）
5. WP035-TD01 兑现：hook_executor 注入化（PipelineEngine 构造注入；service.py 为 composition root；test_td01 双引擎独立实例验证）

事务原则：扩展调用在 engine 内无 DB 事务持有（fire_hook 独立）
