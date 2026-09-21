# Demo V0.1 验收清单

## A. 项目理解

- [ ] 可创建项目。
- [ ] 可导入 Project Method Asset。
- [ ] 系统能抽取 Procedure Model。
- [ ] 系统能区分已明确与未知信息。

## B. Readiness

- [ ] 至少支持 READY / PARTIAL / AMBIGUOUS / MISSING。
- [ ] 能指出至少一个具体 Gap。
- [ ] Gap 有理由和证据来源。

## C. Playbook / Planning

- [ ] Project Playbook 可生成。
- [ ] Playbook 中用户来源与 Research 来源可区分。
- [ ] Planner 基于 Playbook 生成 Task Graph。
- [ ] Graph 可进行基本 Cycle Check。

## D. Execution

- [ ] 至少接入一个真实 Worker。
- [ ] Worker 能接收 Task Contract。
- [ ] Worker 能产生真实 Artifact。
- [ ] Worker 不能直接将 Task 标记 ACCEPTED。

## E. Gate

- [ ] EXECUTION_DONE 后进入 Gate。
- [ ] Gate PASS 后 Task 才 ACCEPTED。
- [ ] Gate FAIL 能保留失败原因。

## F. Automation

- [ ] FULL_AUTO 下可自动进入下一个 READY Task。
- [ ] Resource Governor Lite 可限制并发为 1。
- [ ] 资源不足使用 DEFER_RESOURCE，而不是 FAIL。

## G. UI

- [ ] UI 能看到 Task Graph。
- [ ] UI 能看到 Method Readiness。
- [ ] UI 能看到当前 Worker。
- [ ] UI 能看到 Artifact。
- [ ] UI 能看到 Gate 结果。
- [ ] UI 能看到 Execution Timeline。

## Demo V0.1 PASS

以上核心项通过后：

```text
Demo V0.1 = PASS
```

此时才进入完整 Extension Runtime / Memory / Traceability 等增强建设。


## H. 状态机

- [ ] TaskStatus 与 ProjectStatus 严格符合 `03-核心领域模型.md`。
- [ ] `RUNNING -> ACCEPTED` 被确定性拒绝。
- [ ] `EXECUTION_DONE -> ACCEPTED` 仅在 Gate PASS 时允许。
- [ ] HARD Dependency 仅 `ACCEPTED` 解锁。
- [ ] `DEFER_RESOURCE` 不改变 `READY` 状态。
- [ ] `ACCEPTED` / `COMPLETED` / `CANCELLED` 终态不可静默回退。
- [ ] `40-testing/D03-状态机测试矩阵.md` 全部通过。
