# WP-015 Replanner

> 状态：TODO  
> Gate：PASS / FAIL / BLOCKED

## 前置依赖

```text
WP-007 Dynamic Planner
WP-014 Gate
WP-029 Research Manager
WP-030 Project Playbook 与 Planning Context
```

## 目标

当 Gate FAIL、项目事实变化、Research 更新或用户需求变化时重新规划。

## 流程

```text
Trigger
→ Impact Analysis
→ Capability Coverage Re-assessment
→ Need Research?
→ Project Playbook Update
→ Planning Context Update
→ Replan
```

## 验收

- [ ] Replan 可以触发新 Research。
- [ ] 新 Research 可以使 Playbook 升级版本。
- [ ] 不直接篡改已确认需求。
