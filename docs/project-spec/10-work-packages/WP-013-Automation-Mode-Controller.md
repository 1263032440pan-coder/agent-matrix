# WP-013 Automation Mode Controller

> 状态：DONE（阶段 6 对账批 2026-10-04——Demo D13 + auto-advance 增量）  
> 开发方式：单 Work Package 独立开发  
> Gate：PASS / FAIL / BLOCKED

## 1. 任务目标

实现唯一允许的二态自动化模型：

```text
FULL_AUTO
MANUAL
```

## 2. 前置依赖

```text
WP-002 Project State
WP-011 Event Engine
```

## 3. 核心规则

### FULL_AUTO

在用户已授予权限、安全硬边界和资源限制内：

```text
自动规划
自动调度
自动执行
自动 Gate
自动 Rework / Replan
自动交付推进
```

普通风险或高风险不能自动转换成“询问一下”的第三模式。

如果缺少必须由用户提供的信息：

```text
NEED_USER_INPUT / BLOCKED
```

### MANUAL

AI 可以完成：

```text
分析
生成方案
准备 Task / Work Package
生成执行 Proposal
解释 Gate / Replan 结果
```

但是以下动作必须人工触发/确认：

```text
Task Dispatch
External Write / Execute
Apply Replan
Apply State-Advancing Change
Final Delivery Commit
```

## 4. 核心接口

```text
get_mode(project_id)
set_mode(project_id, FULL_AUTO | MANUAL)
can_advance(action)
create_manual_action(action)
confirm_manual_action(id)
reject_manual_action(id)
record_human_decision()
```

## 5. 禁止实现

```text
任何第三种或混合自动化模式
risk_level -> human approval
```

## 6. 验收条件

- [ ] 系统只接受 FULL_AUTO / MANUAL。
- [ ] FULL_AUTO 不因普通风险自动进入人工等待。
- [ ] MANUAL 的状态推进动作没有人工确认时不得执行。
- [ ] NEED_USER_INPUT 与 ManualAction 数据结构分离。
- [ ] 模式切换可持久化并产生事件。
- [ ] Runtime Extension 不能覆盖项目 Automation Mode。

---

## 对账说明（阶段 6 对账批 2026-10-04）

代码实证: 对应模块 Demo/RH 期交付，测试族全绿（见状态表行）。状态表 TODO→PASS 落账；无新功能增量，纯账实对齐。
