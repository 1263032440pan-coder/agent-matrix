# WP-022 Traceability Graph

> 状态：DONE（2026-10-04，阶段 6 第 3 站）  
> Gate：PASS（依据 tests/test_traceability.py 7 用例）

## 必读

```text
00-global/03-核心领域模型.md
00-global/07-长项目上下文与项目记忆策略.md
20-tracking/RTM-需求追踪矩阵.md
```

## 1. 任务目标

实现运行时 Project Traceability Graph。

## 2. 前置依赖

```text
WP-002 Project State
WP-005 Task Graph
WP-012 Artifact Manager
WP-014 Gate
```

## 3. 追踪链

```text
Goal / Requirement
→ Architecture
→ Task
→ Artifact
→ Test
→ Gate
→ Delivery Item
```

## 4. 核心接口

```text
add_node()
add_edge()
get_upstream()
get_downstream()
get_coverage()
find_gaps()
rebuild_scope()
```

## 5. 验收条件

- [ ] 可建立 Requirement 到 Delivery 的追踪链。
- [ ] 可检查未实现 Requirement。
- [ ] 可检查无测试 Artifact。
- [ ] 可检查无 Gate 的 Task。
- [ ] 可输出 Traceability Gap。
