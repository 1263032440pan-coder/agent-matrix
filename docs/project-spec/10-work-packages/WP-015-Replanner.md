# WP-015 Replanner

> 状态：DONE（2026-09-30——真新域交付；RTM 批次 2 曾误标 D14 交付，本批纠正）  
> Gate：PASS（依据 tests/test_replanner.py 7 用例）

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

- [x] Replan 可以触发新 Research（重评 gap → GapResolver 路由链真实执行；EXPLICITNESS→模型补、DOMAIN/TECHNICAL→五查 miss 建研究——与 WP-029 同一套红线）
- [x] 新 Research 可以使 Playbook 升级版本（RESEARCH_UPDATE 触发 → 新 model → 新 playbook id，旧 playbook 历史保留实测）
- [x] 不直接篡改已确认需求（ACCEPTED Task 状态不动 + MANDATORY 资产 authority/content 逐字不动实测——replan 只产新版本新 Task）

## 实现说明

- `project_runtime/replanner/`（triggers: 四触发源检测 Gate FAIL/资产指纹变化/ANSWERED 研究新增/understanding 快照前进 + baseline 快照协议；service: Replanner 主链 Trigger→normalize 重评→Impact（node provenance 追溯）→gap 路由→Playbook 新版本→DynamicPlanner 幂等 Replan→新 baseline）
- Impact analysis 时序：normalize 之后跑（新资产 ProcedureNode provenance 才可追溯——初版时序错误实测暴露修正）
- DynamicPlanner pending_requests 在 replan 链 surface（知识缺口信号与 WP-029 消费打通）
