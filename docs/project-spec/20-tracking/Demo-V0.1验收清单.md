# Demo V0.1 验收清单

> 验收日期：2026-09-22。每项附证据（测试/阶段）。

## A. 项目理解

- [x] 可创建项目。（D02，tests/test_models.py）
- [x] 可导入 Project Method Asset。（D05，test_method_asset.py 18 tests）
- [x] 系统能抽取 Procedure Model。（D06，test_procedure.py）
- [x] 系统能区分已明确与未知信息。（D06 UNKNOWN 标记 + readiness_status）

## B. Readiness

- [x] 至少支持 READY / PARTIAL / AMBIGUOUS / MISSING。（D07，test_readiness.py）
- [x] 能指出至少一个具体 Gap。（D08，test_gap.py）
- [x] Gap 有理由和证据来源。（D08 source_node_id 回填 + routing strategy）

## C. Playbook / Planning

- [x] Project Playbook 可生成。（D09，test_playbook.py）
- [x] Playbook 中用户来源与 Research 来源可区分。（D09 provenance：source_type/source_ref/authority；V0.1 全 USER_METHOD 如实标注）
- [x] Planner 基于 Playbook 生成 Task Graph。（D10，test_planner.py）
- [x] Graph 可进行基本 Cycle Check。（D04，test_task_graph.py）

## D. Execution

- [x] 至少接入一个真实 Worker。（D11 MockWorker V0.1——无外部进程，真实 FSM/TaskRun/Artifact 管线；goose/ACP 属 Demo 后）
- [x] Worker 能接收 Task Contract。（D11 WorkPackage）
- [x] Worker 能产生真实 Artifact。（D12 落盘 + SHA256 + 版本，test_artifact.py）
- [x] Worker 不能直接将 Task 标记 ACCEPTED。（D11 红线 + D13 FSM actor 防护强制）

## E. Gate

- [x] EXECUTION_DONE 后进入 Gate。（D13 GateCore，非 EXECUTION_DONE 拒绝）
- [x] Gate PASS 后 Task 才 ACCEPTED。（D13，actor=GATE 唯一通道）
- [x] Gate FAIL 能保留失败原因。（D13 GateResult.details 持久化逐 Validator 明细）

## F. Automation

- [x] FULL_AUTO 下可自动进入下一个 READY Task。（D14 Scheduler dispatch_once）
- [x] Resource Governor Lite 可限制并发为 1。（D15 max_workers=1）
- [x] 资源不足使用 DEFER_RESOURCE，而不是 FAIL。（D15 test_defer_at_capacity_task_stays_ready）

## G. UI

- [x] UI 能看到 Task Graph。（D17 Dashboard task-graph section）
- [x] UI 能看到 Method Readiness。（D17 readiness section + gap_count）
- [x] UI 能看到当前 Worker。（D17 current_task + worker_status）
- [x] UI 能看到 Artifact。（D17 artifacts section）
- [x] UI 能看到 Gate 结果。（D17 gate-results section）
- [x] UI 能看到 Execution Timeline。（D17 返修补齐：TaskRun timeline section）

## Demo V0.1 PASS

以上核心项通过后：

```text
Demo V0.1 = PASS
```

此时才进入完整 Extension Runtime / Memory / Traceability 等增强建设。


## H. 状态机

- [x] TaskStatus 与 ProjectStatus 严格符合 `03-核心领域模型.md`。（D03 V1.11 FSM 冻结实现）
- [x] `RUNNING -> ACCEPTED` 被确定性拒绝。（D03/D11 测试 + actor 防护）
- [x] `EXECUTION_DONE -> ACCEPTED` 仅在 Gate PASS 时允许。（D13 FSM actor 参数强制）
- [x] HARD Dependency 仅 `ACCEPTED` 解锁。（D04 test_hard_dep_only_accepted_satisfies；D18 E2E 依赖阻塞断言）
- [x] `DEFER_RESOURCE` 不改变 `READY` 状态。（D15 专测）
- [x] `ACCEPTED` / `COMPLETED` / `CANCELLED` 终态不可静默回退。（D03 FSM 终态集合为空 + D13 专测）
- [x] `40-testing/D03-状态机测试矩阵.md` 全部通过。（test_fsm.py 115+ 用例）

## V1.12 D17.5 Skill Runtime 验收

- [x] `Skill Registry = EMPTY` 时 Runtime 正常工作。（D17A A01 + D18 E2E L1 断言）
- [x] 用户可导入合法声明式 Skill Package。（D17B B01/B02）
- [x] 非法 ZIP 路径 / Schema / 可执行文件被拒绝。（D17B B03-B08）
- [x] Skill 可按 ID + Version 注册并 Enable/Disable。（D17A）
- [x] Project Binding 固定明确版本。（D17A C04 + D18 pin 专测）
- [x] Resolver Rule-first；LLM 只处理完全等价候选。（D17C 四级规则链；V0.1 无 LLM，等价→AMBIGUOUS）
- [x] Optional Skill 缺失不阻塞；Required Skill 缺失形成 Capability Gap。（D17C D04/D05 + D07）
- [x] Loader 只加载当前需要的 Skill 内容。（D17D E02/E03 + 篡改复算）
- [x] Worker Result 可追踪实际 Skill ID / Version / Hash。（D17D E05 + TaskRun payload）
- [x] Skill 不直接修改 Project State / Task State / Task Graph。（D17D E06 双层防线 + ADR-036）
- [x] D17.5 不依赖任何写死领域 Skill ID。（全链仅消费外部 Mock Fixture）